#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trend_logic.py — สมองของ Trend-Following Grid Bot
คำนวณ EMA 50/200 จากข้อมูล OKX เพื่อระบุทิศทางตลาด
"""

import logging
from typing import Tuple

log = logging.getLogger("TrendLogic")


def calculate_ema(prices: list, period: int) -> float:
    """
    คำนวณ Exponential Moving Average (EMA)
    ใช้ SMA ของ `period` แรกเป็นจุดเริ่มต้น แล้วใช้สูตร EMA ต่อ
    """
    if len(prices) < period:
        return prices[-1] if prices else 0.0

    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period  # SMA เริ่มต้น
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def get_trend(market_api, inst_id: str = "ETH-USDT-SWAP") -> Tuple[str, float, float, float]:
    """
    ดึง candle data จาก OKX แล้วคำนวณ EMA 50 และ EMA 200

    Returns:
        (trend, ema50, ema200, current_price)
        trend = "UP" | "DOWN" | "UNKNOWN"

    Logic:
        EMA50 > EMA200 → Trend UP  (ซื้อ long grid)
        EMA50 < EMA200 → Trend DOWN (ไม่เปิด grid ใหม่)
    """
    try:
        # ดึง 210 candles hourly (เพียงพอสำหรับ EMA 200)
        res = market_api.get_candlesticks(instId=inst_id, bar="1H", limit="210")
        if res["code"] != "0" or not res.get("data"):
            log.error(f"get_candlesticks failed: {res}")
            return "UNKNOWN", 0.0, 0.0, 0.0

        # OKX คืน [ts, open, high, low, close, vol, ...] เรียงใหม่→เก่า
        candles = res["data"]
        closes = [float(c[4]) for c in reversed(candles)]
        current_price = closes[-1]

        if len(closes) < 200:
            log.warning(f"ข้อมูลไม่พอ: {len(closes)} candles (ต้องการ 200+)")
            return "UNKNOWN", 0.0, 0.0, current_price

        ema50  = calculate_ema(closes, 50)
        ema200 = calculate_ema(closes, 200)
        trend  = "UP" if ema50 > ema200 else "DOWN"

        log.info(f"📊 Trend | Price: ${current_price:,.2f} | "
                 f"EMA50: ${ema50:,.2f} | EMA200: ${ema200:,.2f} | "
                 f"{'🟢 UP' if trend == 'UP' else '🔴 DOWN'}")

        return trend, ema50, ema200, current_price

    except Exception as e:
        log.error(f"get_trend error: {e}")
        return "UNKNOWN", 0.0, 0.0, 0.0


def get_trend_range(current_price: float, trend: str) -> Tuple[str, str]:
    """
    คำนวณ Grid Range ตาม Trend (Trailing Grid)

    Trend UP:
        Lower = current_price × 0.97  (-3%  ใต้ราคาตลาด)
        Upper = current_price × 1.13  (+13% เหนือราคาตลาด)
        รวม range = 16% เน้นฝั่ง Buy

    Trend DOWN:
        ไม่ควรเปิด grid แต่ถ้าจำเป็น:
        Lower = current_price × 0.87  (-13%)
        Upper = current_price × 1.03  (+3%)
        รวม range = 16% เน้นฝั่ง Sell
    """
    if trend == "UP":
        lower = round(current_price * 0.97, 0)
        upper = round(current_price * 1.13, 0)
    else:
        lower = round(current_price * 0.87, 0)
        upper = round(current_price * 1.03, 0)

    return str(int(lower)), str(int(upper))
