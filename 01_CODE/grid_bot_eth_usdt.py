#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║         ETH/USDT Grid Bot — OKX Built-in Grid API           ║
║         Deploy: GitHub Actions (cron ทุก 5 นาที)            ║
║         Mode  : Single-run (ไม่มี while loop)               ║
║                                                              ║
║  Features:                                                   ║
║   ✅ Auto-restart เมื่อ OKX หยุดบอทโดยไม่ตั้งใจ            ║
║   ✅ Telegram แจ้งเตือนทันทีเมื่อบอทหยุด/restart/start    ║
║   ✅ should_run flag ป้องกัน restart เมื่อหยุดแบบตั้งใจ    ║
║   ✅ ตรวจสอบราคาก่อน restart (ป้องกัน Stop Loss loop)      ║
╚══════════════════════════════════════════════════════════════╝

Flow:
  - ครั้งแรก : รัน MODE=start   → สั่ง OKX เปิด Grid + แจ้ง Telegram
  - ทุก 5 นาที: รัน MODE=monitor → ตรวจสถานะ → ถ้าหยุด → restart อัตโนมัติ
  - หยุด Grid : รัน MODE=stop   → สั่ง OKX ปิด Grid + แจ้ง Telegram
"""

import os
import sys
import logging
import requests
from datetime import datetime, timezone, date

try:
    import okx.Grid as GridTrading
    import okx.MarketData as MarketData
except ImportError:
    print("❌ กรุณาติดตั้ง: pip install python-okx")
    sys.exit(1)

try:
    from supabase import create_client, Client
except ImportError:
    print("❌ กรุณาติดตั้ง: pip install supabase")
    sys.exit(1)


# ============================================================
#  ⚙️  CONFIGURATION
# ============================================================
API_KEY       = os.environ.get("OKX_API_KEY",    "YOUR_OKX_API_KEY")
API_SECRET    = os.environ.get("OKX_API_SECRET",  "YOUR_OKX_SECRET")
PASSPHRASE    = os.environ.get("OKX_PASSPHRASE",  "YOUR_PASSPHRASE")
FLAG          = os.environ.get("OKX_FLAG",        "1")   # "1"=Demo "0"=Live

SUPABASE_URL  = os.environ.get("SUPABASE_URL",    "YOUR_SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY",    "YOUR_SUPABASE_KEY")

# Telegram
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

MODE          = os.environ.get("MODE", "monitor") # start | monitor | stop

# Grid Parameters
INST_ID       = "ETH-USDT-SWAP"
GRID_UPPER    = os.environ.get("GRID_UPPER", "2300")
GRID_LOWER    = os.environ.get("GRID_LOWER", "1800")
GRID_COUNT    = os.environ.get("GRID_COUNT", "25")
LEVERAGE      = os.environ.get("LEVERAGE",   "3")
DIRECTION     = "long"
RUN_TYPE      = "1"           # 1 = Arithmetic
STOP_LOSS_PX  = os.environ.get("STOP_LOSS",  "1700")
TOTAL_CAPITAL = float(os.environ.get("CAPITAL", "200"))


# ============================================================
#  📝  LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("GridBot")


# ============================================================
#  📱  TELEGRAM
# ============================================================
def send_telegram(message: str):
    """ส่งข้อความแจ้งเตือนผ่าน Telegram Bot"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("⚠️ Telegram ไม่ได้ตั้งค่า — ข้ามการแจ้งเตือน")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=10)
        if resp.status_code == 200:
            log.info("📱 ส่ง Telegram สำเร็จ")
        else:
            log.warning(f"⚠️ Telegram ส่งไม่สำเร็จ: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        log.warning(f"⚠️ Telegram error: {e}")


# ============================================================
#  🗄️  SUPABASE
# ============================================================
class DB:
    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def save_trades(self, trades: list, algo_id: str):
        if not trades:
            return 0
        rows = [{
            "algo_id":    algo_id,
            "order_id":   t.get("ordId", ""),
            "inst_id":    INST_ID,
            "side":       t.get("side", ""),
            "price":      float(t.get("avgPx") or t.get("px") or 0),
            "size":       float(t.get("sz", 0)),
            "profit":     float(t.get("pnl") or 0),
            "state":      t.get("state", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        } for t in trades]
        # Deduplicate by order_id (OKX อาจส่ง ordId ซ้ำใน batch เดียวกัน)
        rows = list({r["order_id"]: r for r in rows}.values())
        self.client.table("trades").upsert(rows, on_conflict="order_id").execute()
        return len(rows)

    def update_status(self, algo_id: str, state: str, price: float,
                      profit: float, trade_count: int):
        pct = (profit / TOTAL_CAPITAL) * 100 if TOTAL_CAPITAL else 0
        self.client.table("bot_status").upsert({
            "bot_id":        algo_id,
            "inst_id":       INST_ID,
            "is_running":    state in ("running", "pause"),
            "current_price": price,
            "trade_count":   trade_count,
            "total_profit":  profit,
            "profit_pct":    round(pct, 6),
            "grid_lower":    float(GRID_LOWER),
            "grid_upper":    float(GRID_UPPER),
            "leverage":      int(LEVERAGE),
            "capital":       TOTAL_CAPITAL,
            "algo_state":    state,
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }, on_conflict="bot_id").execute()

    def set_should_run(self, algo_id: str, value: bool):
        """ตั้งค่า should_run flag — True=บอทควรรัน, False=หยุดแบบตั้งใจ"""
        self.client.table("bot_status").update({
            "should_run": value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("bot_id", algo_id).execute()
        log.info(f"  🏷️  should_run → {value}")

    def get_should_run(self, algo_id: str) -> bool:
        """ดึงค่า should_run flag (default=True ถ้าไม่มีข้อมูล)"""
        res = self.client.table("bot_status") \
                  .select("should_run") \
                  .eq("bot_id", algo_id) \
                  .limit(1).execute()
        if res.data:
            return res.data[0].get("should_run", True)
        return True  # ถ้าไม่มีข้อมูล → ถือว่าควรรัน

    def get_algo_id(self) -> str:
        """ดึง algo_id ล่าสุดจาก Supabase (กรอง leverage + bot_tag IS NULL = Original Grid Bot)"""
        res = self.client.table("bot_status") \
                  .select("bot_id") \
                  .eq("inst_id", INST_ID) \
                  .eq("leverage", int(LEVERAGE)) \
                  .is_("bot_tag", "null") \
                  .order("updated_at", desc=True) \
                  .limit(1).execute()
        if res.data:
            return res.data[0]["bot_id"]
        return ""

    def get_trade_count(self, algo_id: str) -> int:
        """นับจำนวน trades ทั้งหมดของ algo นี้"""
        res = self.client.table("trades") \
                  .select("order_id") \
                  .eq("algo_id", algo_id).execute()
        return len(res.data or [])

    def get_total_trade_count(self) -> int:
        """นับจำนวน trades ทั้งหมดทุก algo"""
        res = self.client.table("trades").select("order_id").execute()
        return len(res.data or [])

    def save_pnl_snapshot(self, algo_id: str, pnl: float):
        """บันทึก PnL ของ algo ที่หยุดแล้ว — เพื่อไม่ให้กำไรหายตอน restart"""
        self.client.table("bot_status").update({
            "total_profit": pnl,
            "algo_state":   "stopped_final",
            "updated_at":   datetime.now(timezone.utc).isoformat(),
        }).eq("bot_id", algo_id).execute()
        log.info(f"  💾 บันทึก PnL snapshot: algo={algo_id} pnl=${pnl:.4f}")

    def save_daily_pnl(self, algo_id: str, pnl: float, trade_count: int):
        today = date.today().isoformat()
        self.client.table("pnl_daily").upsert({
            "bot_id":      algo_id,
            "date":        today,
            "pnl":         pnl,
            "trade_count": trade_count,
            "updated_at":  datetime.now(timezone.utc).isoformat(),
        }, on_conflict="bot_id,date").execute()


# ============================================================
#  🤖  GRID MANAGER
# ============================================================
class GridManager:
    def __init__(self):
        self.grid_api   = GridTrading.GridAPI(
                            API_KEY, API_SECRET, PASSPHRASE, False, FLAG)
        self.market_api = MarketData.MarketAPI(flag=FLAG)
        self.db         = DB()

    def get_price(self) -> float:
        try:
            res = self.market_api.get_ticker(instId=INST_ID)
            if res["code"] == "0":
                return float(res["data"][0]["last"])
        except Exception as e:
            log.error(f"get_price: {e}")
        return 0.0

    def get_running_algo_id(self) -> str:
        """
        ตรวจว่า Original Grid Bot ที่รู้จักรันอยู่บน OKX ไหม
        ใช้ known_id จาก Supabase (bot_tag IS NULL) เป็นหลัก
        """
        known_id = self.db.get_algo_id()
        if not known_id:
            return ""
        try:
            res = self.grid_api.grid_orders_algo_pending(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0" and res["data"]:
                for algo in res["data"]:
                    if algo["algoId"] == known_id:
                        return algo["algoId"]
        except Exception as e:
            log.error(f"get_running_algo_id: {e}")
        return ""

    def get_algo_profit_from_okx(self, algo_id: str) -> float:
        """
        ✅ FIX: ดึงกำไรจริงจาก OKX ระดับ algo
        sub_orders[].pnl = 0 เสมอ (OKX spec)
        กำไรที่แท้จริงอยู่ใน algo object เท่านั้น
        """
        # 1. ลอง pending ก่อน (บอทกำลังรัน)
        try:
            res = self.grid_api.grid_orders_algo_pending(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0":
                for d in res.get("data", []):
                    if d.get("algoId") == algo_id:
                        pnl = float(d.get("pnl") or d.get("profit") or 0)
                        log.info(f"  📊 Algo PnL (pending): ${pnl:.4f}")
                        return pnl
        except Exception as e:
            log.error(f"get_algo_profit pending: {e}")
        # 2. ลอง history (บอทหยุดแล้ว)
        try:
            res = self.grid_api.grid_orders_algo_history(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0":
                for d in res.get("data", []):
                    if d.get("algoId") == algo_id:
                        pnl = float(d.get("pnl") or d.get("profit") or 0)
                        log.info(f"  📊 Algo PnL (history): ${pnl:.4f}")
                        return pnl
        except Exception as e:
            log.error(f"get_algo_profit history: {e}")
        return 0.0

    def get_historical_profit_from_okx(self) -> float:
        """
        ✅ FIX: ดึงกำไรสะสมจาก algo ที่หยุดแล้วทั้งหมด
        นับทุก algo ใน history เพื่อให้ได้ยอดรวมถูกต้อง
        แม้บอทจะ restart 20 ครั้ง กำไรก็ไม่หาย
        """
        total = 0.0
        try:
            res = self.grid_api.grid_orders_algo_history(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0":
                for d in res.get("data", []):
                    total += float(d.get("pnl") or 0)
                log.info(f"  📜 Historical PnL รวม (algo หยุดแล้ว): ${total:.4f}")
        except Exception as e:
            log.error(f"get_historical_profit: {e}")
        return total

    def _do_start_algo(self, price: float) -> str:
        """
        สั่ง OKX เปิด Grid จริงๆ — คืน algo_id ถ้าสำเร็จ, "" ถ้าล้มเหลว
        แยกออกมาเพื่อให้ cmd_start() และ auto-restart ใช้ร่วมกันได้
        """
        params = {
            "instId":      INST_ID,
            "algoOrdType": "contract_grid",
            "maxPx":       GRID_UPPER,
            "minPx":       GRID_LOWER,
            "gridNum":     GRID_COUNT,
            "runType":     RUN_TYPE,
            "direction":   DIRECTION,
            "lever":       LEVERAGE,
            "sz":          str(int(TOTAL_CAPITAL)),
        }
        if STOP_LOSS_PX:
            params["slTriggerPx"] = STOP_LOSS_PX

        res = self.grid_api.grid_order_algo(**params)
        if res["code"] == "0":
            return res["data"][0]["algoId"]
        else:
            log.error(f"❌ เปิด Grid ไม่ได้: code={res.get('code')} msg={res.get('msg')} data={res.get('data')}")
            return ""

    # ── MODE: start ───────────────────────────────────────────
    def cmd_start(self):
        """สั่ง OKX เปิด Grid + ตั้ง should_run=True + แจ้ง Telegram"""
        log.info("=" * 55)
        log.info("  🚀 MODE: START GRID")
        log.info(f"  Range: ${GRID_LOWER} – ${GRID_UPPER} | Grids: {GRID_COUNT}")
        log.info(f"  Leverage: {LEVERAGE}x | Capital: ${int(TOTAL_CAPITAL)} | SL: ${STOP_LOSS_PX}")
        log.info(f"  Mode: {'🧪 DEMO' if FLAG == '1' else '🔴 LIVE'}")
        log.info("=" * 55)

        # เช็คว่ามี Grid รันอยู่แล้วหรือยัง
        existing = self.get_running_algo_id()
        if existing:
            log.warning(f"⚠️  มี Grid รันอยู่แล้ว Algo ID: {existing}")
            log.warning("   ถ้าต้องการเปิดใหม่ ให้ MODE=stop ก่อน")
            return

        # เช็คราคาว่าอยู่ใน range
        price = self.get_price()
        if price == 0:
            log.error("❌ ดึงราคาไม่ได้")
            sys.exit(1)
        if not (float(GRID_LOWER) < price < float(GRID_UPPER)):
            log.error(f"❌ ราคา ${price:,.2f} อยู่นอก range ${GRID_LOWER}–${GRID_UPPER}")
            send_telegram(
                f"❌ <b>Grid Bot {LEVERAGE}x เปิดไม่ได้</b>\n"
                f"💲 ETH: <b>${price:,.2f}</b> อยู่นอก range\n"
                f"📊 Range: ${GRID_LOWER}–${GRID_UPPER}\n"
                f"กรุณาปรับ range ก่อน restart"
            )
            sys.exit(1)

        try:
            algo_id = self._do_start_algo(price)
            if algo_id:
                log.info(f"✅ OKX เปิด Grid สำเร็จ! Algo ID: {algo_id}")
                self.db.update_status(algo_id, "running", price, 0.0, 0)
                self.db.set_should_run(algo_id, True)
                env_label = "🧪 DEMO" if FLAG == "1" else "🔴 LIVE"
                send_telegram(
                    f"✅ <b>Grid Bot {LEVERAGE}x เปิดแล้ว!</b>\n"
                    f"📊 Range: ${GRID_LOWER}–${GRID_UPPER} | Grids: {GRID_COUNT}\n"
                    f"💰 Capital: ${int(TOTAL_CAPITAL)} | SL: ${STOP_LOSS_PX}\n"
                    f"💲 ETH ตอนนี้: ${price:,.2f}\n"
                    f"🔗 Algo ID: <code>{algo_id}</code>\n"
                    f"{env_label}"
                )
            else:
                sys.exit(1)
        except Exception as e:
            log.error(f"cmd_start error: {e}")
            sys.exit(1)

    # ── MODE: monitor ─────────────────────────────────────────
    def cmd_monitor(self):
        """
        ดึงข้อมูลจาก OKX → บันทึก Supabase → จบ
        ถ้าบอทหยุดและ should_run=True → auto-restart + แจ้ง Telegram
        ถ้าราคานอก range → แจ้งเตือนและไม่ restart (ป้องกัน Stop Loss loop)
        """
        log.info(f"📡 MONITOR — {datetime.now().strftime('%H:%M:%S')}")

        # หา algo_id
        algo_id = self.get_running_algo_id()
        if not algo_id:
            algo_id = self.db.get_algo_id()
        if not algo_id:
            log.warning("⚠️  ไม่พบ Grid ที่รันอยู่ — รัน MODE=start ก่อน")
            return

        # ดึงราคาปัจจุบัน
        price = self.get_price()

        # ดึงสถานะ Grid + PnL จาก OKX (อ่านจาก algo level — ถูกต้อง)
        state = "running"
        algo_profit = 0.0  # ✅ กำไรจาก algo ปัจจุบัน (ไม่ใช่ sub-order)
        try:
            res = self.grid_api.grid_orders_algo_pending(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0":
                found = next((d for d in res["data"]
                              if d["algoId"] == algo_id), None)
                if found:
                    state = found.get("state", "running")
                    # ✅ FIX BUG 1: อ่าน PnL จาก algo object โดยตรง
                    # sub_orders[].pnl = 0 เสมอ (OKX spec)
                    algo_profit = float(found.get("pnl") or found.get("profit") or 0)
                    log.info(f"  📊 Algo PnL (OKX): ${algo_profit:.4f}")
                else:
                    state = "stopped"
        except Exception as e:
            log.error(f"get state error: {e}")

        log.info(f"  💲 ETH: ${price:,.2f} | State: {state}")

        # ── AUTO-RESTART LOGIC ─────────────────────────────────
        if state == "stopped":
            should_run = self.db.get_should_run(algo_id)
            log.info(f"  🏷️  should_run = {should_run}")

            if should_run:
                # ตรวจสอบราคา ก่อน restart เสมอ
                price_in_range = float(GRID_LOWER) < price < float(GRID_UPPER)

                if price_in_range:
                    # ✅ ราคาอยู่ใน range → restart ปลอดภัย
                    log.warning("⚠️  บอทหยุดโดย OKX — กำลัง restart อัตโนมัติ...")

                    # ✅ FIX BUG 3: บันทึก PnL ของ algo เก่าก่อน restart
                    # เพื่อไม่ให้กำไรหายเมื่อได้ algo_id ใหม่
                    old_pnl = self.get_algo_profit_from_okx(algo_id)
                    self.db.save_pnl_snapshot(algo_id, old_pnl)
                    log.info(f"  💾 Snapshot PnL algo เก่า: ${old_pnl:.4f}")

                    send_telegram(
                        f"⚠️ <b>Grid Bot {LEVERAGE}x หยุดโดย OKX!</b>\n"
                        f"💲 ETH: ${price:,.2f} (ยังอยู่ใน range)\n"
                        f"💰 PnL algo นี้: ${old_pnl:.4f}\n"
                        f"🔄 กำลัง restart อัตโนมัติ..."
                    )
                    try:
                        new_algo_id = self._do_start_algo(price)
                        if new_algo_id:
                            log.info(f"✅ Restart สำเร็จ! Algo ID: {new_algo_id}")
                            self.db.update_status(new_algo_id, "running", price, 0.0, 0)
                            self.db.set_should_run(new_algo_id, True)
                            send_telegram(
                                f"✅ <b>Grid Bot {LEVERAGE}x restart สำเร็จ!</b>\n"
                                f"💲 ETH: ${price:,.2f}\n"
                                f"📊 Range: ${GRID_LOWER}–${GRID_UPPER}\n"
                                f"🔗 Algo ID ใหม่: <code>{new_algo_id}</code>"
                            )
                        else:
                            send_telegram(
                                f"❌ <b>Grid Bot {LEVERAGE}x restart ล้มเหลว!</b>\n"
                                f"กรุณาตรวจสอบ GitHub Actions log"
                            )
                    except Exception as e:
                        log.error(f"auto-restart error: {e}")
                        send_telegram(
                            f"❌ <b>Grid Bot {LEVERAGE}x restart error!</b>\n"
                            f"Error: {str(e)[:100]}"
                        )
                else:
                    # ⛔ ราคาอยู่นอก range → อาจ Stop Loss ทำงาน → ไม่ restart
                    log.warning(f"⛔ ราคา ${price:,.2f} อยู่นอก range — ไม่ restart อัตโนมัติ")
                    self.db.set_should_run(algo_id, False)  # หยุดพยายาม restart
                    self.db.update_status(algo_id, "stopped", price, 0.0, 0)
                    send_telegram(
                        f"🚨 <b>Grid Bot {LEVERAGE}x หยุด + ราคานอก range!</b>\n"
                        f"💲 ETH: <b>${price:,.2f}</b>\n"
                        f"📊 Range: ${GRID_LOWER}–${GRID_UPPER}\n"
                        f"⚠️ อาจเกิดจาก Stop Loss trigger\n"
                        f"🔕 ไม่ restart อัตโนมัติ — กรุณาตรวจสอบและ restart มือเองถ้าต้องการ"
                    )
            else:
                # should_run=False → หยุดแบบตั้งใจ → ไม่ restart
                log.info("ℹ️  บอทหยุดแบบตั้งใจ (should_run=False) — ไม่ restart")
                self.db.update_status(algo_id, "stopped", price, 0.0, 0)
            return  # จบ — ไม่ต้องดึง trades เพราะบอทหยุดแล้ว

        # ── บอทกำลังรันปกติ → ดึงข้อมูลและบันทึก ──────────────
        # ดึง trades ที่ filled ใหม่
        new_trades = []
        try:
            res = self.grid_api.grid_sub_orders(
                algoId=algo_id,
                algoOrdType="contract_grid",
                type="filled"
            )
            if res["code"] == "0":
                new_trades = res.get("data", [])
        except Exception as e:
            log.error(f"get sub_orders error: {e}")

        # บันทึก trades ใหม่ (สำหรับ audit trail — profit ใน sub-order = 0 ตาม OKX spec)
        saved = self.db.save_trades(new_trades, algo_id)
        if saved:
            log.info(f"  💾 บันทึก {saved} trades ใหม่")

        # ✅ FIX BUG 1+3: คำนวณ PnL ที่ถูกต้อง
        # - algo_profit = กำไรจาก algo ปัจจุบัน (อ่านจาก OKX แล้วตั้งแต่ state check)
        # - historical_profit = กำไรสะสมจาก algo ที่หยุดแล้ว (OKX history)
        historical_profit = self.get_historical_profit_from_okx()
        total_profit = algo_profit + historical_profit
        trade_count  = self.db.get_trade_count(algo_id)

        log.info(f"  💵 Current algo PnL: ${algo_profit:.4f}")
        log.info(f"  📜 Historical PnL:   ${historical_profit:.4f}")
        log.info(f"  💰 Total PnL:        ${total_profit:.4f}")

        # อัปเดตสถานะ
        self.db.update_status(algo_id, state, price, total_profit, trade_count)
        self.db.save_daily_pnl(algo_id, total_profit, trade_count)

        pct = (total_profit / TOTAL_CAPITAL) * 100 if TOTAL_CAPITAL else 0
        log.info(f"  📈 PnL: ${total_profit:.4f} ({pct:.4f}%) | Trades: {trade_count}")
        log.info("  ✅ Monitor เสร็จ")

    # ── MODE: stop ────────────────────────────────────────────
    def cmd_stop(self):
        """สั่ง OKX หยุด Grid + ตั้ง should_run=False + แจ้ง Telegram"""
        log.info("⛔ MODE: STOP GRID")

        algo_id = self.get_running_algo_id()
        if not algo_id:
            algo_id = self.db.get_algo_id()
        if not algo_id:
            log.warning("⚠️  ไม่พบ Grid ที่รันอยู่")
            return

        try:
            res = self.grid_api.grid_stop_order_algo(
                algoId=algo_id,
                instId=INST_ID,
                algoOrdType="contract_grid",
                stopType="1"
            )
            if res["code"] == "0":
                price = self.get_price()
                log.info(f"✅ หยุด Grid สำเร็จ | Algo ID: {algo_id}")
                self.db.update_status(algo_id, "stopped", price, 0.0, 0)
                self.db.set_should_run(algo_id, False)  # ← ตั้งใจหยุด → ไม่ auto-restart
                send_telegram(
                    f"🛑 <b>Grid Bot {LEVERAGE}x หยุดแล้ว</b>\n"
                    f"ℹ️ หยุดแบบตั้งใจ (manual stop)\n"
                    f"💲 ETH: ${price:,.2f}\n"
                    f"🔗 Algo ID: <code>{algo_id}</code>\n"
                    f"🔕 Auto-restart ถูกปิดแล้ว"
                )
            else:
                log.error(f"❌ หยุดไม่ได้: {res.get('msg')}")
        except Exception as e:
            log.error(f"cmd_stop error: {e}")


# ============================================================
#  🚀  ENTRY POINT
# ============================================================
def main():
    env_label = "🧪 DEMO" if FLAG == "1" else "🔴 LIVE"
    log.info(f"🤖 Grid Bot {LEVERAGE}x | Mode: {MODE.upper()} | {env_label}")

    bot = GridManager()

    if MODE == "start":
        bot.cmd_start()
    elif MODE == "monitor":
        bot.cmd_monitor()
    elif MODE == "stop":
        bot.cmd_stop()
    else:
        log.error(f"❌ MODE ไม่ถูกต้อง: '{MODE}' (ต้องเป็น start | monitor | stop)")
        sys.exit(1)


if __name__ == "__main__":
    main()
