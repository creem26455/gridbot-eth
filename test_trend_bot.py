#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║      ETH/USDT Trend-Following Grid Bot v1                   ║
║      Side-car Bot — ไม่กระทบ Grid Bot เดิม 100%            ║
║      Strategy: EMA 50/200 Trailing Grid                     ║
╚══════════════════════════════════════════════════════════════╝

Flow:
  MODE=start   → คำนวณ Trend → วาง Grid ตาม Trend (1 ครั้ง)
  MODE=monitor → ดึงข้อมูล OKX → บันทึก Supabase (ทุก 10 นาที)
  MODE=stop    → หยุด Grid (1 ครั้ง)

Bot IDs: trend_v1_2x | trend_v1_3x | trend_v1_5x
"""

import os
import sys
import logging
import requests
from datetime import datetime, timezone, date

try:
    import okx.Grid as Grid
    import okx.MarketData as MarketData
except ImportError:
    print("❌ กรุณาติดตั้ง: pip install python-okx")
    sys.exit(1)

try:
    from supabase import create_client, Client
except ImportError:
    print("❌ กรุณาติดตั้ง: pip install supabase")
    sys.exit(1)

from trend_logic import get_trend, get_trend_range


# ============================================================
#  ⚙️  CONFIGURATION
# ============================================================
API_KEY       = os.environ.get("OKX_API_KEY",    "YOUR_OKX_API_KEY")
API_SECRET    = os.environ.get("OKX_API_SECRET",  "YOUR_OKX_SECRET")
PASSPHRASE    = os.environ.get("OKX_PASSPHRASE",  "YOUR_PASSPHRASE")
FLAG          = os.environ.get("OKX_FLAG",        "1")   # "1"=Demo "0"=Live

SUPABASE_URL  = os.environ.get("SUPABASE_URL",    "YOUR_SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY",    "YOUR_SUPABASE_KEY")

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID",   "")

MODE          = os.environ.get("MODE", "monitor")   # start | monitor | stop
LEVERAGE      = os.environ.get("LEVERAGE", "3")
BOT_TAG       = "trend_v1"                          # แยกจาก grid bot เดิม

INST_ID       = "ETH-USDT-SWAP"
GRID_COUNT    = "25"
RUN_TYPE      = "1"           # 1 = Arithmetic
TOTAL_CAPITAL = float(os.environ.get("CAPITAL", "1600"))

# Stop Loss offset ตาม leverage (ยิ่ง leverage สูง SL ใกล้ขึ้น)
SL_OFFSET = {"2": 0.12, "3": 0.10, "5": 0.08}


# ============================================================
#  📝  LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("TrendBot")


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
#  🗄️  SUPABASE — ใช้ตารางเดิม แต่ filter ด้วย bot_tag
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
        rows = list({r["order_id"]: r for r in rows}.values())  # dedup by order_id
        self.client.table("trades").upsert(rows, on_conflict="order_id").execute()
        return len(rows)

    def update_status(self, algo_id: str, state: str, price: float,
                      profit: float, trade_count: int,
                      grid_lower: float = 0.0, grid_upper: float = 0.0,
                      trend: str = "UNKNOWN"):
        pct = (profit / TOTAL_CAPITAL) * 100
        self.client.table("bot_status").upsert({
            "bot_id":        algo_id,
            "bot_tag":       BOT_TAG,               # "trend_v1" — แยกจากบอทเดิม
            "inst_id":       INST_ID,
            "is_running":    state in ("running", "pause"),
            "current_price": price,
            "trade_count":   trade_count,
            "total_profit":  profit,
            "profit_pct":    round(pct, 6),
            "grid_lower":    grid_lower,
            "grid_upper":    grid_upper,
            "leverage":      int(LEVERAGE),
            "capital":       TOTAL_CAPITAL,
            "algo_state":    state,
            "algo_tag":      trend,                 # เก็บ trend ล่าสุดไว้ด้วย
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }, on_conflict="bot_id").execute()

    def get_algo_id(self) -> str:
        """ดึง algo_id ของ trend bot จาก Supabase (กรองด้วย bot_tag + leverage)"""
        res = self.client.table("bot_status") \
                  .select("bot_id") \
                  .eq("bot_tag", BOT_TAG) \
                  .eq("leverage", int(LEVERAGE)) \
                  .order("updated_at", desc=True) \
                  .limit(1).execute()
        if res.data:
            return res.data[0]["bot_id"]
        return ""

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

    def get_total_profit(self, algo_id: str) -> tuple:
        res = self.client.table("trades") \
                  .select("profit") \
                  .eq("algo_id", algo_id).execute()
        trades = res.data or []
        total  = sum(float(t["profit"]) for t in trades)
        return total, len(trades)

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
#  🤖  TREND GRID MANAGER
# ============================================================
class TrendGridManager:
    def __init__(self):
        self.grid_api   = Grid.GridAPI(API_KEY, API_SECRET, PASSPHRASE, flag=FLAG)
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
        หา algo_id ที่รันอยู่บน OKX
        ใช้ known_id จาก Supabase เป็นหลัก เพื่อไม่ปนกับ Grid Bot เดิม
        """
        try:
            known_id = self.db.get_algo_id()
            res = self.grid_api.grid_orders_algo_pending(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0" and res["data"]:
                for algo in res["data"]:
                    # ถ้ารู้ algo_id อยู่แล้ว → match ตรงๆ (แม่นยำ 100%)
                    if known_id and algo["algoId"] == known_id:
                        return algo["algoId"]
                    # Fallback: ถ้ายังไม่มี known_id ใช้ leverage
                    if not known_id and str(algo.get("lever", "")) == str(LEVERAGE):
                        return algo["algoId"]
        except Exception as e:
            log.error(f"get_running_algo_id: {e}")
        return ""

    # ── MODE: start ──────────────────────────────────────────
    def _do_start_algo(self, price: float, trend: str) -> str:
        """
        สั่ง OKX เปิด Grid จริงๆ — คืน algo_id ถ้าสำเร็จ, "" ถ้าล้มเหลว
        แยกออกมาเพื่อให้ cmd_start() และ auto-restart ใช้ร่วมกันได้
        """
        # Trend UP → คำนวณ Range แบบ Trailing
        grid_lower, grid_upper = get_trend_range(price, trend)
        sl_offset = SL_OFFSET.get(str(LEVERAGE), 0.10)
        stop_loss = str(int(float(grid_lower) * (1 - sl_offset)))

        log.info(f"🟢 Trend {trend} | Range: ${grid_lower}–${grid_upper}")

        # ตรวจสอบราคาอยู่ใน range
        if not (float(grid_lower) < price < float(grid_upper)):
            log.error(f"❌ ราคา ${price:,.2f} อยู่นอก range — ข้ามการเปิด")
            return ""

        # สั่ง OKX เปิด Grid
        params = {
            "instId":      INST_ID,
            "algoOrdType": "contract_grid",
            "maxPx":       grid_upper,
            "minPx":       grid_lower,
            "gridNum":     GRID_COUNT,
            "runType":     RUN_TYPE,
            "direction":   "long",
            "lever":       LEVERAGE,
            "sz":          str(int(TOTAL_CAPITAL)),
            "slTriggerPx": stop_loss,
        }

        try:
            res = self.grid_api.grid_order_algo(**params)
            if res["code"] == "0":
                algo_id = res["data"][0]["algoId"]
                log.info(f"✅ Trend Grid เปิดสำเร็จ! Algo ID: {algo_id}")
                self.db.update_status(
                    algo_id, "running", price, 0.0, 0,
                    float(grid_lower), float(grid_upper), trend
                )
                return algo_id
            else:
                log.error(f"❌ เปิด Grid ไม่ได้: code={res.get('code')} msg={res.get('msg')}")
                return ""
        except Exception as e:
            log.error(f"_do_start_algo error: {e}")
            return ""

    def cmd_start(self):
        log.info("=" * 60)
        log.info(f"  🚀 TREND BOT {LEVERAGE}x — MODE: START")
        log.info(f"  Capital: ${TOTAL_CAPITAL} | {'🧪 DEMO' if FLAG == '1' else '🔴 LIVE'}")
        log.info("=" * 60)

        # เช็คว่ามี Grid รันอยู่แล้วหรือยัง
        existing = self.get_running_algo_id()
        if existing:
            log.warning(f"⚠️  Trend Grid {LEVERAGE}x รันอยู่แล้ว: {existing}")
            log.warning("   ถ้าต้องการเปิดใหม่ ให้รัน MODE=stop ก่อน")
            return

        # คำนวณ Trend จาก EMA 50/200 (ข้อมูล 210 ชั่วโมงย้อนหลัง)
        trend, ema50, ema200, price = get_trend(self.market_api, INST_ID)

        if trend == "UNKNOWN":
            log.error("❌ คำนวณ Trend ไม่ได้ — ข้ามการเปิด Grid")
            return

        if price == 0:
            log.error("❌ ดึงราคาไม่ได้")
            sys.exit(1)

        # Trend DOWN → ไม่เปิด Grid ในรอบนี้
        if trend == "DOWN":
            log.warning("🔴 Trend DOWN — ไม่เปิด Grid รอบนี้")
            log.warning(f"   EMA50 (${ema50:,.2f}) < EMA200 (${ema200:,.2f})")
            log.warning("   Monitor จะเช็คใหม่ใน 10 นาที")
            return

        # เรียก helper method เพื่อเปิด Grid
        algo_id = self._do_start_algo(price, trend)
        if algo_id:
            self.db.set_should_run(algo_id, True)
        else:
            sys.exit(1)

    # ── MODE: monitor ────────────────────────────────────────
    def cmd_monitor(self):
        log.info(f"📡 TREND MONITOR {LEVERAGE}x — {datetime.now().strftime('%H:%M:%S')}")

        algo_id = self.get_running_algo_id()
        if not algo_id:
            algo_id = self.db.get_algo_id()
        if not algo_id:
            log.warning("⚠️  ไม่พบ Trend Grid — รัน MODE=start ก่อน")
            return

        # ดึงสถานะจาก OKX
        state = "running"
        grid_lower = 0.0
        grid_upper = 0.0
        try:
            res = self.grid_api.grid_orders_algo_pending(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0":
                found = next((d for d in res["data"]
                              if d["algoId"] == algo_id), None)
                if found:
                    state      = found.get("state", "running")
                    grid_lower = float(found.get("minPx", 0))
                    grid_upper = float(found.get("maxPx", 0))
                else:
                    state = "stopped"
        except Exception as e:
            log.error(f"get state error: {e}")

        price = self.get_price()

        # ── AUTO-RESTART LOGIC ─────────────────────────────────
        if state == "stopped":
            should_run = self.db.get_should_run(algo_id)
            log.info(f"  🏷️  should_run = {should_run}")

            if should_run:
                # ดึง Trend ล่าสุด เพื่อหา range
                trend, ema50, ema200, _ = get_trend(self.market_api, INST_ID)

                # คำนวณ range ตาม Trend
                tr_lower, tr_upper = get_trend_range(price, trend)

                # ตรวจสอบราคา ก่อน restart เสมอ
                price_in_range = float(tr_lower) < price < float(tr_upper)

                if price_in_range and trend == "UP":
                    # ✅ ราคาอยู่ใน range และ Trend UP → restart ปลอดภัย
                    log.warning("⚠️  บอทหยุดโดย OKX — กำลัง restart อัตโนมัติ...")
                    send_telegram(
                        f"⚠️ <b>Trend Bot {LEVERAGE}x หยุดโดย OKX!</b>\n"
                        f"💲 ETH: ${price:,.2f} (ยังอยู่ใน range)\n"
                        f"📊 Trend: {trend}\n"
                        f"🔄 กำลัง restart อัตโนมัติ..."
                    )
                    try:
                        new_algo_id = self._do_start_algo(price, trend)
                        if new_algo_id:
                            log.info(f"✅ Restart สำเร็จ! Algo ID: {new_algo_id}")
                            self.db.update_status(new_algo_id, "running", price, 0.0, 0,
                                                 float(tr_lower), float(tr_upper), trend)
                            self.db.set_should_run(new_algo_id, True)
                            send_telegram(
                                f"✅ <b>Trend Bot {LEVERAGE}x restart สำเร็จ!</b>\n"
                                f"💲 ETH: ${price:,.2f}\n"
                                f"📊 Range: ${tr_lower}–${tr_upper} | Trend: {trend}\n"
                                f"🔗 Algo ID ใหม่: <code>{new_algo_id}</code>"
                            )
                        else:
                            send_telegram(
                                f"❌ <b>Trend Bot {LEVERAGE}x restart ล้มเหลว!</b>\n"
                                f"กรุณาตรวจสอบ GitHub Actions log"
                            )
                    except Exception as e:
                        log.error(f"auto-restart error: {e}")
                        send_telegram(
                            f"❌ <b>Trend Bot {LEVERAGE}x restart error!</b>\n"
                            f"Error: {str(e)[:100]}"
                        )
                else:
                    # ⛔ ราคาอยู่นอก range หรือ Trend ไม่ใช่ UP → อาจ Stop Loss ทำงาน → ไม่ restart
                    log.warning(f"⛔ ราคา ${price:,.2f} อยู่นอก range หรือ Trend != UP — ไม่ restart อัตโนมัติ")
                    self.db.set_should_run(algo_id, False)  # หยุดพยายาม restart
                    self.db.update_status(algo_id, "stopped", price, 0.0, 0)
                    send_telegram(
                        f"🚨 <b>Trend Bot {LEVERAGE}x หยุด + ราคานอก range!</b>\n"
                        f"💲 ETH: <b>${price:,.2f}</b>\n"
                        f"📊 Range: ${tr_lower}–${tr_upper} | Trend: {trend}\n"
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

        saved = self.db.save_trades(new_trades, algo_id)
        if saved:
            log.info(f"  💾 บันทึก {saved} trades ใหม่")

        total_profit, trade_count = self.db.get_total_profit(algo_id)

        # ดึง Trend ล่าสุดเพื่อ log + บันทึก
        trend, ema50, ema200, _ = get_trend(self.market_api, INST_ID)

        self.db.update_status(
            algo_id, state, price, total_profit, trade_count,
            grid_lower, grid_upper, trend
        )
        self.db.save_daily_pnl(algo_id, total_profit, trade_count)

        pct = (total_profit / TOTAL_CAPITAL) * 100
        log.info(f"  💲 ETH: ${price:,.2f} | State: {state}")
        log.info(f"  📊 Trend: {trend} | EMA50: ${ema50:,.2f} | EMA200: ${ema200:,.2f}")
        log.info(f"  💵 PnL: ${total_profit:.4f} ({pct:.4f}%) | Trades: {trade_count}")
        log.info("  ✅ Trend Monitor เสร็จ")

    # ── MODE: stop ───────────────────────────────────────────
    def cmd_stop(self):
        log.info(f"⛔ TREND BOT {LEVERAGE}x — MODE: STOP")

        algo_id = self.get_running_algo_id()
        if not algo_id:
            algo_id = self.db.get_algo_id()
        if not algo_id:
            log.warning("⚠️  ไม่พบ Trend Grid ที่รันอยู่")
            return

        try:
            res = self.grid_api.grid_stop_order_algo(
                algoId=algo_id,
                instId=INST_ID,
                algoOrdType="contract_grid",
                stopType="1"
            )
            if res["code"] == "0":
                log.info(f"✅ หยุด Trend Grid สำเร็จ | Algo ID: {algo_id}")
                self.db.update_status(
                    algo_id, "stopped", self.get_price(), 0.0, 0
                )
                self.db.set_should_run(algo_id, False)  # หยุดแบบตั้งใจ — ไม่ restart อัตโนมัติ
                send_telegram(
                    f"⛔ <b>Trend Bot {LEVERAGE}x หยุดแล้ว</b>\n"
                    f"🏷️  should_run = False (ไม่ restart อัตโนมัติ)"
                )
            else:
                log.error(f"❌ หยุดไม่ได้: {res}")
        except Exception as e:
            log.error(f"cmd_stop error: {e}")


# ============================================================
#  🚀  ENTRY POINT
# ============================================================
def main():
    log.info(f"🤖 Trend Grid Bot {LEVERAGE}x | Mode: {MODE.upper()} | "
             f"{'🧪 DEMO' if FLAG == '1' else '🔴 LIVE'}")

    bot = TrendGridManager()

    if MODE == "start":
        bot.cmd_start()
    elif MODE == "monitor":
        bot.cmd_monitor()
    elif MODE == "stop":
        bot.cmd_stop()
    else:
        log.error(f"❌ MODE ไม่ถูกต้อง: '{MODE}' (start | monitor | stop)")
        sys.exit(1)


if __name__ == "__main__":
    main()
