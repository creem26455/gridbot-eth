#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
â         ETH/USDT Grid Bot â OKX Built-in Grid API           â
â         Deploy: GitHub Actions (cron à¸à¸¸à¸ 5 à¸à¸²à¸à¸µ)            â
â         Mode  : Single-run (à¹à¸¡à¹à¸¡à¸µ while loop)               â
â                                                              â
â  Features:                                                   â
â   â Auto-restart à¹à¸¡à¸·à¹à¸­ OKX à¸«à¸¢à¸¸à¸à¸à¸­à¸à¹à¸à¸¢à¹à¸¡à¹à¸à¸±à¹à¸à¹à¸            â
â   â Telegram à¹à¸à¹à¸à¹à¸à¸·à¸­à¸à¸à¸±à¸à¸à¸µà¹à¸¡à¸·à¹à¸­à¸à¸­à¸à¸«à¸¢à¸¸à¸/restart/start    â
â   â should_run flag à¸à¹à¸­à¸à¸à¸±à¸ restart à¹à¸¡à¸·à¹à¸­à¸«à¸¢à¸¸à¸à¹à¸à¸à¸à¸±à¹à¸à¹à¸    â
â   â à¸à¸£à¸§à¸à¸ªà¸­à¸à¸£à¸²à¸à¸²à¸à¹à¸­à¸ restart (à¸à¹à¸­à¸à¸à¸±à¸ Stop Loss loop)      â
ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

Flow:
  - à¸à¸£à¸±à¹à¸à¹à¸£à¸ : à¸£à¸±à¸ MODE=start   â à¸ªà¸±à¹à¸ OKX à¹à¸à¸´à¸ Grid + à¹à¸à¹à¸ Telegram
  - à¸à¸¸à¸ 5 à¸à¸²à¸à¸µ: à¸£à¸±à¸ MODE=monitor â à¸à¸£à¸§à¸à¸ªà¸à¸²à¸à¸° â à¸à¹à¸²à¸«à¸¢à¸¸à¸ â restart à¸­à¸±à¸à¹à¸à¸¡à¸±à¸à¸´
  - à¸«à¸¢à¸¸à¸ Grid : à¸£à¸±à¸ MODE=stop   â à¸ªà¸±à¹à¸ OKX à¸à¸´à¸ Grid + à¹à¸à¹à¸ Telegram
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
    print("â à¸à¸£à¸¸à¸à¸²à¸à¸´à¸à¸à¸±à¹à¸: pip install python-okx")
    sys.exit(1)

try:
    from supabase import create_client, Client
except ImportError:
    print("â à¸à¸£à¸¸à¸à¸²à¸à¸´à¸à¸à¸±à¹à¸: pip install supabase")
    sys.exit(1)


# ============================================================
#  âï¸  CONFIGURATION
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
#  ð  LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("GridBot")


# ============================================================
#  ð±  TELEGRAM
# ============================================================
def send_telegram(message: str):
    """à¸ªà¹à¸à¸à¹à¸­à¸à¸§à¸²à¸¡à¹à¸à¹à¸à¹à¸à¸·à¸­à¸à¸à¹à¸²à¸ Telegram Bot"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("â ï¸ Telegram à¹à¸¡à¹à¹à¸à¹à¸à¸±à¹à¸à¸à¹à¸² â à¸à¹à¸²à¸¡à¸à¸²à¸£à¹à¸à¹à¸à¹à¸à¸·à¸­à¸")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=10)
        if resp.status_code == 200:
            log.info("ð± à¸ªà¹à¸ Telegram à¸ªà¸³à¹à¸£à¹à¸")
        else:
            log.warning(f"â ï¸ Telegram à¸ªà¹à¸à¹à¸¡à¹à¸ªà¸³à¹à¸£à¹à¸: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        log.warning(f"â ï¸ Telegram error: {e}")


# ============================================================
#  ðï¸  SUPABASE
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
        # Deduplicate by order_id (OKX à¸­à¸²à¸à¸ªà¹à¸ ordId à¸à¹à¸³à¹à¸ batch à¹à¸à¸µà¸¢à¸§à¸à¸±à¸)
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
        """à¸à¸±à¹à¸à¸à¹à¸² should_run flag â True=à¸à¸­à¸à¸à¸§à¸£à¸£à¸±à¸, False=à¸«à¸¢à¸¸à¸à¹à¸à¸à¸à¸±à¹à¸à¹à¸"""
        self.client.table("bot_status").update({
            "should_run": value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("bot_id", algo_id).execute()
        log.info(f"  ð·ï¸  should_run â {value}")

    def get_should_run(self, algo_id: str) -> bool:
        """à¸à¸¶à¸à¸à¹à¸² should_run flag (default=True à¸à¹à¸²à¹à¸¡à¹à¸¡à¸µà¸à¹à¸­à¸¡à¸¹à¸¥)"""
        res = self.client.table("bot_status") \
                  .select("should_run") \
                  .eq("bot_id", algo_id) \
                  .limit(1).execute()
        if res.data:
            return res.data[0].get("should_run", True)
        return True  # à¸à¹à¸²à¹à¸¡à¹à¸¡à¸µà¸à¹à¸­à¸¡à¸¹à¸¥ â à¸à¸·à¸­à¸§à¹à¸²à¸à¸§à¸£à¸£à¸±à¸

    def get_algo_id(self) -> str:
        """à¸à¸¶à¸ algo_id à¸¥à¹à¸²à¸ªà¸¸à¸à¸à¸²à¸ Supabase (à¸à¸£à¸­à¸ leverage + bot_tag IS NULL = Original Grid Bot)"""
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

    def get_total_profit(self, algo_id: str) -> tuple:
        """à¸à¸¶à¸à¸à¸³à¹à¸£à¸ªà¸°à¸ªà¸¡à¹à¸¥à¸°à¸à¸³à¸à¸§à¸à¹à¸à¸£à¸à¸à¸²à¸ Supabase"""
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
#  ð¤  GRID MANAGER
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
        à¸à¸£à¸§à¸à¸§à¹à¸² Original Grid Bot à¸à¸µà¹à¸£à¸¹à¹à¸à¸±à¸à¸£à¸±à¸à¸­à¸¢à¸¹à¹à¸à¸ OKX à¹à¸«à¸¡
        à¹à¸à¹ known_id à¸à¸²à¸ Supabase (bot_tag IS NULL) à¹à¸à¹à¸à¸«à¸¥à¸±à¸
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

    def _do_start_algo(self, price: float) -> str:
        """
        à¸ªà¸±à¹à¸ OKX à¹à¸à¸´à¸ Grid à¸à¸£à¸´à¸à¹ â à¸à¸·à¸ algo_id à¸à¹à¸²à¸ªà¸³à¹à¸£à¹à¸, "" à¸à¹à¸²à¸¥à¹à¸¡à¹à¸«à¸¥à¸§
        à¹à¸¢à¸à¸­à¸­à¸à¸¡à¸²à¹à¸à¸·à¹à¸­à¹à¸«à¹ cmd_start() à¹à¸¥à¸° auto-restart à¹à¸à¹à¸£à¹à¸§à¸¡à¸à¸±à¸à¹à¸à¹
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
            log.error(f"â à¹à¸à¸´à¸ Grid à¹à¸¡à¹à¹à¸à¹: code={res.get('code')} msg={res.get('msg')} data={res.get('data')}")
            return ""

    # ââ MODE: start âââââââââââââââââââââââââââââââââââââââââââ
    def cmd_start(self):
        """à¸ªà¸±à¹à¸ OKX à¹à¸à¸´à¸ Grid + à¸à¸±à¹à¸ should_run=True + à¹à¸à¹à¸ Telegram"""
        log.info("=" * 55)
        log.info("  ð MODE: START GRID")
        log.info(f"  Range: ${GRID_LOWER} â ${GRID_UPPER} | Grids: {GRID_COUNT}")
        log.info(f"  Leverage: {LEVERAGE}x | Capital: ${int(TOTAL_CAPITAL)} | SL: ${STOP_LOSS_PX}")
        log.info(f"  Mode: {'ð§ª DEMO' if FLAG == '1' else 'ð´ LIVE'}")
        log.info("=" * 55)

        # à¹à¸à¹à¸à¸§à¹à¸²à¸¡à¸µ Grid à¸£à¸±à¸à¸­à¸¢à¸¹à¹à¹à¸¥à¹à¸§à¸«à¸£à¸·à¸­à¸¢à¸±à¸
        existing = self.get_running_algo_id()
        if existing:
            log.warning(f"â ï¸  à¸¡à¸µ Grid à¸£à¸±à¸à¸­à¸¢à¸¹à¹à¹à¸¥à¹à¸§ Algo ID: {existing}")
            log.warning("   à¸à¹à¸²à¸à¹à¸­à¸à¸à¸²à¸£à¹à¸à¸´à¸à¹à¸«à¸¡à¹ à¹à¸«à¹ MODE=stop à¸à¹à¸­à¸")
            return

        # à¹à¸à¹à¸à¸£à¸²à¸à¸²à¸§à¹à¸²à¸­à¸¢à¸¹à¹à¹à¸ range
        price = self.get_price()
        if price == 0:
            log.error("â à¸à¸¶à¸à¸£à¸²à¸à¸²à¹à¸¡à¹à¹à¸à¹")
            sys.exit(1)
        if not (float(GRID_LOWER) < price < float(GRID_UPPER)):
            log.error(f"â à¸£à¸²à¸à¸² ${price:,.2f} à¸­à¸¢à¸¹à¹à¸à¸­à¸ range ${GRID_LOWER}â${GRID_UPPER}")
            send_telegram(
                f"â <b>Grid Bot {LEVERAGE}x à¹à¸à¸´à¸à¹à¸¡à¹à¹à¸à¹</b>\n"
                f"ð² ETH: <b>${price:,.2f}</b> à¸­à¸¢à¸¹à¹à¸à¸­à¸ range\n"
                f"ð Range: ${GRID_LOWER}â${GRID_UPPER}\n"
                f"à¸à¸£à¸¸à¸à¸²à¸à¸£à¸±à¸ range à¸à¹à¸­à¸ restart"
            )
            sys.exit(1)

        try:
            algo_id = self._do_start_algo(price)
            if algo_id:
                log.info(f"â OKX à¹à¸à¸´à¸ Grid à¸ªà¸³à¹à¸£à¹à¸! Algo ID: {algo_id}")
                self.db.update_status(algo_id, "running", price, 0.0, 0)
                self.db.set_should_run(algo_id, True)
                env_label = "ð§ª DEMO" if FLAG == "1" else "ð´ LIVE"
                send_telegram(
                    f"â <b>Grid Bot {LEVERAGE}x à¹à¸à¸´à¸à¹à¸¥à¹à¸§!</b>\n"
                    f"ð Range: ${GRID_LOWER}â${GRID_UPPER} | Grids: {GRID_COUNT}\n"
                    f"ð° Capital: ${int(TOTAL_CAPITAL)} | SL: ${STOP_LOSS_PX}\n"
                    f"ð² ETH à¸à¸­à¸à¸à¸µà¹: ${price:,.2f}\n"
                    f"ð Algo ID: <code>{algo_id}</code>\n"
                    f"{env_label}"
                )
            else:
                sys.exit(1)
        except Exception as e:
            log.error(f"cmd_start error: {e}")
            sys.exit(1)

    # ââ MODE: monitor âââââââââââââââââââââââââââââââââââââââââ
    def cmd_monitor(self):
        """
        à¸à¸¶à¸à¸à¹à¸­à¸¡à¸¹à¸¥à¸à¸²à¸ OKX â à¸à¸±à¸à¸à¸¶à¸ Supabase â à¸à¸
        à¸à¹à¸²à¸à¸­à¸à¸«à¸¢à¸¸à¸à¹à¸¥à¸° should_run=True â auto-restart + à¹à¸à¹à¸ Telegram
        à¸à¹à¸²à¸£à¸²à¸à¸²à¸à¸­à¸ range â à¹à¸à¹à¸à¹à¸à¸·à¸­à¸à¹à¸¥à¸°à¹à¸¡à¹ restart (à¸à¹à¸­à¸à¸à¸±à¸ Stop Loss loop)
        """
        log.info(f"ð¡ MONITOR â {datetime.now().strftime('%H:%M:%S')}")

        # à¸«à¸² algo_id
        algo_id = self.get_running_algo_id()
        if not algo_id:
            algo_id = self.db.get_algo_id()
        if not algo_id:
            log.warning("â ï¸  à¹à¸¡à¹à¸à¸ Grid à¸à¸µà¹à¸£à¸±à¸à¸­à¸¢à¸¹à¹ â à¸£à¸±à¸ MODE=start à¸à¹à¸­à¸")
            return

        # à¸à¸¶à¸à¸£à¸²à¸à¸²à¸à¸±à¸à¸à¸¸à¸à¸±à¸
        price = self.get_price()

        # à¸à¸¶à¸à¸ªà¸à¸²à¸à¸° Grid à¸à¸²à¸ OKX
        state = "running"
        try:
            res = self.grid_api.grid_orders_algo_pending(
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

        log.info(f"  ð² ETH: ${price:,.2f} | State: {state}")

        # ââ AUTO-RESTART LOGIC âââââââââââââââââââââââââââââââââ
        if state == "stopped":
            should_run = self.db.get_should_run(algo_id)
            log.info(f"  ð·ï¸  should_run = {should_run}")

            if should_run:
                # à¸à¸£à¸§à¸à¸ªà¸­à¸à¸£à¸²à¸à¸² à¸à¹à¸­à¸ restart à¹à¸ªà¸¡à¸­
                price_in_range = float(GRID_LOWER) < price < float(GRID_UPPER)

                if price_in_range:
                    # â à¸£à¸²à¸à¸²à¸­à¸¢à¸¹à¹à¹à¸ range â restart à¸à¸¥à¸­à¸à¸ à¸±à¸¢
                    log.warning("â ï¸  à¸à¸­à¸à¸«à¸¢à¸¸à¸à¹à¸à¸¢ OKX â à¸à¸³à¸¥à¸±à¸ restart à¸­à¸±à¸à¹à¸à¸¡à¸±à¸à¸´...")
                    send_telegram(
                        f"â ï¸ <b>Grid Bot {LEVERAGE}x à¸«à¸¢à¸¸à¸à¹à¸à¸¢ OKX!</b>\n"
                        f"ð² ETH: ${price:,.2f} (à¸¢à¸±à¸à¸­à¸¢à¸¹à¹à¹à¸ range)\n"
                        f"ð à¸à¸³à¸¥à¸±à¸ restart à¸­à¸±à¸à¹à¸à¸¡à¸±à¸à¸´..."
                    )
                    try:
                        new_algo_id = self._do_start_algo(price)
                        if new_algo_id:
                            log.info(f"â Restart à¸ªà¸³à¹à¸£à¹à¸! Algo ID: {new_algo_id}")
                            self.db.update_status(new_algo_id, "running", price, 0.0, 0)
                            self.db.set_should_run(new_algo_id, True)
                            send_telegram(
                                f"â <b>Grid Bot {LEVERAGE}x restart à¸ªà¸³à¹à¸£à¹à¸!</b>\n"
                                f"ð² ETH: ${price:,.2f}\n"
                                f"ð Range: ${GRID_LOWER}â${GRID_UPPER}\n"
                                f"ð Algo ID à¹à¸«à¸¡à¹: <code>{new_algo_id}</code>"
                            )
                        else:
                            send_telegram(
                                f"â <b>Grid Bot {LEVERAGE}x restart à¸¥à¹à¸¡à¹à¸«à¸¥à¸§!</b>\n"
                                f"à¸à¸£à¸¸à¸à¸²à¸à¸£à¸§à¸à¸ªà¸­à¸ GitHub Actions log"
                            )
                    except Exception as e:
                        log.error(f"auto-restart error: {e}")
                        send_telegram(
                            f"â <b>Grid Bot {LEVERAGE}x restart error!</b>\n"
                            f"Error: {str(e)[:100]}"
                        )
                else:
                    # â à¸£à¸²à¸à¸²à¸­à¸¢à¸¹à¹à¸à¸­à¸ range â à¸­à¸²à¸ Stop Loss à¸à¸³à¸à¸²à¸ â à¹à¸¡à¹ restart
                    log.warning(f"â à¸£à¸²à¸à¸² ${price:,.2f} à¸­à¸¢à¸¹à¹à¸à¸­à¸ range â à¹à¸¡à¹ restart à¸­à¸±à¸à¹à¸à¸¡à¸±à¸à¸´")
                    self.db.set_should_run(algo_id, False)  # à¸«à¸¢à¸¸à¸à¸à¸¢à¸²à¸¢à¸²à¸¡ restart
                    self.db.update_status(algo_id, "stopped", price, 0.0, 0)
                    send_telegram(
                        f"ð¨ <b>Grid Bot {LEVERAGE}x à¸«à¸¢à¸¸à¸ + à¸£à¸²à¸à¸²à¸à¸­à¸ range!</b>\n"
                        f"ð² ETH: <b>${price:,.2f}</b>\n"
                        f"ð Range: ${GRID_LOWER}â${GRID_UPPER}\n"
                        f"â ï¸ à¸­à¸²à¸à¹à¸à¸´à¸à¸à¸²à¸ Stop Loss trigger\n"
                        f"ð à¹à¸¡à¹ restart à¸­à¸±à¸à¹à¸à¸¡à¸±à¸à¸´ â à¸à¸£à¸¸à¸à¸²à¸à¸£à¸§à¸à¸ªà¸­à¸à¹à¸¥à¸° restart à¸¡à¸·à¸­à¹à¸­à¸à¸à¹à¸²à¸à¹à¸­à¸à¸à¸²à¸£"
                    )
            else:
                # should_run=False â à¸«à¸¢à¸¸à¸à¹à¸à¸à¸à¸±à¹à¸à¹à¸ â à¹à¸¡à¹ restart
                log.info("â¹ï¸  à¸à¸­à¸à¸«à¸¢à¸¸à¸à¹à¸à¸à¸à¸±à¹à¸à¹à¸ (should_run=False) â à¹à¸¡à¹ restart")
                self.db.update_status(algo_id, "stopped", price, 0.0, 0)
            return  # à¸à¸ â à¹à¸¡à¹à¸à¹à¸­à¸à¸à¸¶à¸ trades à¹à¸à¸£à¸²à¸°à¸à¸­à¸à¸«à¸¢à¸¸à¸à¹à¸¥à¹à¸§

        # ââ à¸à¸­à¸à¸à¸³à¸¥à¸±à¸à¸£à¸±à¸à¸à¸à¸à¸´ â à¸à¸¶à¸à¸à¹à¸­à¸¡à¸¹à¸¥à¹à¸¥à¸°à¸à¸±à¸à¸à¸¶à¸ ââââââââââââââ
        # à¸à¸¶à¸ trades à¸à¸µà¹ filled à¹à¸«à¸¡à¹
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

        # à¸à¸±à¸à¸à¸¶à¸ trades à¹à¸«à¸¡à¹
        saved = self.db.save_trades(new_trades, algo_id)
        if saved:
            log.info(f"  ð¾ à¸à¸±à¸à¸à¸¶à¸ {saved} trades à¹à¸«à¸¡à¹")

        # à¸à¸¶à¸à¸¢à¸­à¸à¸£à¸§à¸¡à¸à¸²à¸ Supabase
        total_profit, trade_count = self.db.get_total_profit(algo_id)

        # à¸­à¸±à¸à¹à¸à¸à¸ªà¸à¸²à¸à¸°
        self.db.update_status(algo_id, state, price, total_profit, trade_count)
        self.db.save_daily_pnl(algo_id, total_profit, trade_count)

        pct = (total_profit / TOTAL_CAPITAL) * 100 if TOTAL_CAPITAL else 0
        log.info(f"  ðµ PnL: ${total_profit:.4f} ({pct:.4f}%) | Trades: {trade_count}")
        log.info("  â Monitor à¹à¸ªà¸£à¹à¸")

    # ââ MODE: stop ââââââââââââââââââââââââââââââââââââââââââââ
    def cmd_stop(self):
        """à¸ªà¸±à¹à¸ OKX à¸«à¸¢à¸¸à¸ Grid + à¸à¸±à¹à¸ should_run=False + à¹à¸à¹à¸ Telegram"""
        log.info("â MODE: STOP GRID")

        algo_id = self.get_running_algo_id()
        if not algo_id:
            algo_id = self.db.get_algo_id()
        if not algo_id:
            log.warning("â ï¸  à¹à¸¡à¹à¸à¸ Grid à¸à¸µà¹à¸£à¸±à¸à¸­à¸¢à¸¹à¹")
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
                log.info(f"â à¸«à¸¢à¸¸à¸ Grid à¸ªà¸³à¹à¸£à¹à¸ | Algo ID: {algo_id}")
                self.db.update_status(algo_id, "stopped", price, 0.0, 0)
                self.db.set_should_run(algo_id, False)  # â à¸à¸±à¹à¸à¹à¸à¸«à¸¢à¸¸à¸ â à¹à¸¡à¹ auto-restart
                send_telegram(
                    f"ð <b>Grid Bot {LEVERAGE}x à¸«à¸¢à¸¸à¸à¹à¸¥à¹à¸§</b>\n"
                    f"â¹ï¸ à¸«à¸¢à¸¸à¸à¹à¸à¸à¸à¸±à¹à¸à¹à¸ (manual stop)\n"
                    f"ð² ETH: ${price:,.2f}\n"
                    f"ð Algo ID: <code>{algo_id}</code>\n"
                    f"ð Auto-restart à¸à¸¹à¸à¸à¸´à¸à¹à¸¥à¹à¸§"
                )
            else:
                log.error(f"â à¸«à¸¢à¸¸à¸à¹à¸¡à¹à¹à¸à¹: {res.get('msg')}")
        except Exception as e:
            log.error(f"cmd_stop error: {e}")


# ============================================================
#  ð  ENTRY POINT
# ============================================================
def main():
    env_label = "ð§ª DEMO" if FLAG == "1" else "ð´ LIVE"
    log.info(f"ð¤ Grid Bot {LEVERAGE}x | Mode: {MODE.upper()} | {env_label}")

    bot = GridManager()

    if MODE == "start":
        bot.cmd_start()
    elif MODE == "monitor":
        bot.cmd_monitor()
    elif MODE == "stop":
        bot.cmd_stop()
    else:
        log.error(f"â MODE à¹à¸¡à¹à¸à¸¹à¸à¸à¹à¸­à¸: '{MODE}' (à¸à¹à¸­à¸à¹à¸à¹à¸ start | monitor | stop)")
        sys.exit(1)


if __name__ == "__main__":
    main()
