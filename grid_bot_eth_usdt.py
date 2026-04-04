#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║         ETH/USDT Grid Bot — OKX Built-in Grid API           ║
║         Deploy: Google Cloud Run + Cloud Scheduler          ║
║         Mode  : Single-run (ไม่มี while loop)               ║
║                 Cloud Scheduler เรียกทุก 1 นาที             ║
╚══════════════════════════════════════════════════════════════╝

Flow:
  - ครั้งแรก : รัน MODE=start  → สั่ง OKX เปิด Grid (1 ครั้ง)
  - ทุก 1 นาที: รัน MODE=monitor → ดึงข้อมูล OKX → บันทึก Supabase → จบ
  - หยุด Grid : รัน MODE=stop   → สั่ง OKX ปิด Grid (1 ครั้ง)

วิธีตั้งค่า MODE:
  Environment variable: MODE=start | monitor | stop
  หรือ default = "monitor"
"""

import os
import sys
import logging
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
#  ⚙️  CONFIGURATION — อ่านจาก Environment Variables
#      (ตั้งค่าใน Cloud Run → Edit & Deploy → Variables)
# ============================================================
API_KEY       = os.environ.get("OKX_API_KEY",    "YOUR_OKX_API_KEY")
API_SECRET    = os.environ.get("OKX_API_SECRET",  "YOUR_OKX_SECRET")
PASSPHRASE    = os.environ.get("OKX_PASSPHRASE",  "YOUR_PASSPHRASE")
FLAG          = os.environ.get("OKX_FLAG",        "1")   # "1"=Demo "0"=Live

SUPABASE_URL  = os.environ.get("SUPABASE_URL",    "YOUR_SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY",    "YOUR_SUPABASE_KEY")

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
TOTAL_CAPITAL = float(os.environ.get("CAPITAL", "1690"))


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
            "profit":     float(t.get("pnl", 0)),
            "state":      t.get("state", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        } for t in trades]
        self.client.table("trades").upsert(rows, on_conflict="order_id").execute()
        return len(rows)

    def update_status(self, algo_id: str, state: str, price: float,
                      profit: float, trade_count: int):
        pct = (profit / TOTAL_CAPITAL) * 100
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

    def get_algo_id(self) -> str:
        """ดึง algo_id ล่าสุดจาก Supabase (กรอง leverage เดียวกัน)"""
        res = self.client.table("bot_status") \
                  .select("bot_id") \
                  .eq("inst_id", INST_ID) \
                  .eq("leverage", int(LEVERAGE)) \
                  .order("updated_at", desc=True) \
                  .limit(1).execute()
        if res.data:
            return res.data[0]["bot_id"]
        return ""

    def get_total_profit(self, algo_id: str) -> tuple:
        """ดึงกำไรสะสมและจำนวนเทรดจาก Supabase"""
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
        }).execute()


# ============================================================
#  🤖  GRID MANAGER
# ============================================================
class GridManager:
    def __init__(self):
        self.grid_api   = GridTrading.GridTradingAPI(
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
        """ตรวจว่ามี Grid leverage เดิมรันอยู่บน OKX ไหม"""
        try:
            res = self.grid_api.get_grid_algo_order_list(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0" and res["data"]:
                for algo in res["data"]:
                    if str(algo.get("lever", "")) == str(LEVERAGE):
                        return algo["algoId"]
        except Exception as e:
            log.error(f"get_running_algo_id: {e}")
        return ""

    # ── MODE: start ───────────────────────────────────────────
    def cmd_start(self):
        """สั่ง OKX เปิด Grid (รันครั้งเดียว)"""
        log.info("=" * 55)
        log.info("  🚀 MODE: START GRID")
        log.info(f"  Range: ${GRID_LOWER} – ${GRID_UPPER} | Grids: {GRID_COUNT}")
        log.info(f"  Leverage: {LEVERAGE}x | SL: ${STOP_LOSS_PX}")
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
            sys.exit(1)

        # สั่ง OKX เปิด Grid
        params = {
            "instId":      INST_ID,
            "algoOrdType": "contract_grid",
            "maxPx":       GRID_UPPER,
            "minPx":       GRID_LOWER,
            "gridNum":     GRID_COUNT,
            "runType":     RUN_TYPE,
            "direction":   DIRECTION,
            "lever":       LEVERAGE,
        }
        if STOP_LOSS_PX:
            params["slTriggerPx"] = STOP_LOSS_PX
            params["slOrdPx"]     = "-1"

        try:
            res = self.grid_api.place_grid_algo_order(**params)
            if res["code"] == "0":
                algo_id = res["data"][0]["algoId"]
                log.info(f"✅ OKX เปิด Grid สำเร็จ!")
                log.info(f"   Algo ID: {algo_id}")
                log.info(f"   OKX จัดการ orders ทั้งหมดเองแล้ว 🎉")
                # บันทึก algo_id ลง Supabase
                self.db.update_status(algo_id, "running", price, 0.0, 0)
            else:
                log.error(f"❌ เปิด Grid ไม่ได้: {res.get('msg')}")
                sys.exit(1)
        except Exception as e:
            log.error(f"cmd_start error: {e}")
            sys.exit(1)

    # ── MODE: monitor ─────────────────────────────────────────
    def cmd_monitor(self):
        """
        ดึงข้อมูลจาก OKX → บันทึก Supabase → จบ
        Cloud Scheduler เรียก mode นี้ทุก 1 นาที
        """
        log.info(f"📡 MONITOR — {datetime.now().strftime('%H:%M:%S')}")

        # หา algo_id
        algo_id = self.get_running_algo_id()
        if not algo_id:
            # ลองดึงจาก Supabase
            algo_id = self.db.get_algo_id()
        if not algo_id:
            log.warning("⚠️  ไม่พบ Grid ที่รันอยู่ — รัน MODE=start ก่อน")
            return

        # ดึงสถานะ Grid จาก OKX
        state = "running"
        try:
            res = self.grid_api.get_grid_algo_order_list(
                algoOrdType="contract_grid", instId=INST_ID)
            if res["code"] == "0":
                found = next((d for d in res["data"]
                              if d["algoId"] == algo_id), None)
                if found:
                    state = found.get("state", "running")
                else:
                    state = "stopped"
        except Exception as e:
            log.error(f"get state error: {e}")

        # ดึงราคาปัจจุบัน
        price = self.get_price()

        # ดึง trades ที่ filled ใหม่
        new_trades = []
        try:
            res = self.grid_api.get_grid_algo_sub_orders(
                algoId=algo_id,
                algoOrdType="contract_grid",
                type="filled"
            )
            if res["code"] == "0":
                new_trades = res.get("data", [])
        except Exception as e:
            log.error(f"get sub_orders error: {e}")

        # บันทึก trades ใหม่
        saved = self.db.save_trades(new_trades, algo_id)
        if saved:
            log.info(f"  💾 บันทึก {saved} trades ใหม่")

        # ดึงยอดรวมจาก Supabase
        total_profit, trade_count = self.db.get_total_profit(algo_id)

        # อัปเดตสถานะ
        self.db.update_status(algo_id, state, price, total_profit, trade_count)
        self.db.save_daily_pnl(algo_id, total_profit, trade_count)

        pct = (total_profit / TOTAL_CAPITAL) * 100
        log.info(f"  💲 ETH: ${price:,.2f} | State: {state}")
        log.info(f"  💵 PnL: ${total_profit:.4f} ({pct:.4f}%) | Trades: {trade_count}")
        log.info("  ✅ Monitor เสร็จ — Cloud Run จะ shutdown")

    # ── MODE: stop ────────────────────────────────────────────
    def cmd_stop(self):
        """สั่ง OKX หยุด Grid (รันครั้งเดียว)"""
        log.info("⛔ MODE: STOP GRID")

        algo_id = self.get_running_algo_id()
        if not algo_id:
            algo_id = self.db.get_algo_id()
        if not algo_id:
            log.warning("⚠️  ไม่พบ Grid ที่รันอยู่")
            return

        try:
            res = self.grid_api.stop_grid_algo_order(
                algoId=algo_id,
                instId=INST_ID,
                algoOrdType="contract_grid",
                stopType="1"
            )
            if res["code"] == "0":
                log.info(f"✅ หยุด Grid สำเร็จ | Algo ID: {algo_id}")
                self.db.update_status(algo_id, "stopped",
                                      self.get_price(), 0.0, 0)
            else:
                log.error(f"❌ หยุดไม่ได้: {res.get('msg')}")
        except Exception as e:
            log.error(f"cmd_stop error: {e}")


# ============================================================
#  🚀  ENTRY POINT
# ============================================================
def main():
    log.info(f"🤖 Grid Bot | Mode: {MODE.upper()} | "
             f"{'🧪 DEMO' if FLAG == '1' else '🔴 LIVE'}")

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
