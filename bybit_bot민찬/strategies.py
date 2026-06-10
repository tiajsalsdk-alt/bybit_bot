import os
import pandas as pd
import numpy as np
from config import ADX_TREND, FVG_SL_ATR_BUFFER, FVG_RISK_CAP_ATR_MULT

import logging
log = logging.getLogger(__name__)

os.system('')
COLOR_CYAN  = '\033[96m'
COLOR_RESET = '\033[0m'


def get_signal(df: pd.DataFrame, htf_adx: float, htf_ema: float,
               symbol: str = "Unknown",
               df_htf: pd.DataFrame = None) -> tuple[str, str, float, float] | None:
    """
    ADX >= ADX_TREND(30) 추세장에서 15분봉 FVG 신호 반환.
    반환: (side, engine, entry_price, sl_price) 또는 None
    """
    if len(df) < 10:
        return None

    if htf_adx < ADX_TREND:
        print(f"  [Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - ADX {htf_adx:.1f} < {ADX_TREND}")
        return None

    c1  = df.iloc[-3]
    c3  = df.iloc[-1]
    atr = c3["atr"]
    stoch_k = c3.get("stoch_k", np.nan)

    if pd.isna(atr) or atr <= 0:
        return None

    curr_close = c3["close"]

    # Bullish FVG: c3 저점 > c1 고점 (빈 공간 상향)
    if c3["low"] > c1["high"] and curr_close > htf_ema:
        if not pd.isna(stoch_k) and stoch_k >= 80:
            print(f"  [Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - StochRSI 과열 ({stoch_k:.0f})")
            return None
        entry = (c1["high"] + c3["low"]) / 2   # CE 50% 할인 타점
        sl    = c1["low"] - FVG_SL_ATR_BUFFER * atr
        risk  = entry - sl
        if risk <= 0 or risk > FVG_RISK_CAP_ATR_MULT * atr:
            print(f"  [Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - 리스크 캡 초과")
            return None
        return "Buy", "FVG", entry, sl

    # Bearish FVG: c3 고점 < c1 저점 (빈 공간 하향)
    if c3["high"] < c1["low"] and curr_close < htf_ema:
        if not pd.isna(stoch_k) and stoch_k <= 20:
            print(f"  [Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - StochRSI 과매도 ({stoch_k:.0f})")
            return None
        entry = c1["low"]                        # 숏은 풀필 타점 유지
        sl    = c1["high"] + FVG_SL_ATR_BUFFER * atr
        risk  = sl - entry
        if risk <= 0 or risk > FVG_RISK_CAP_ATR_MULT * atr:
            print(f"  [Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - 리스크 캡 초과")
            return None
        return "Sell", "FVG", entry, sl

    print(f"  [Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - FVG 조건 미달")
    return None
