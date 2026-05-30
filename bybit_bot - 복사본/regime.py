import pandas as pd
from enum import Enum
from config import ADX_TREND, ADX_SIDEWAYS

class Regime(Enum):
    SIDEWAYS_UP   = "SIDEWAYS_UP"   # 거시적 상승 중 횡보 (Long 전용)
    SIDEWAYS_DOWN = "SIDEWAYS_DOWN" # 거시적 하락 중 횡보 (Short 전용)
    UPTREND       = "UPTREND"       # 강한 상승 추세
    DOWNTREND     = "DOWNTREND"     # 강한 하락 추세
    DEADZONE      = "DEADZONE"      # 관망 구간 (ADX 20~25)

def _has_bullish_bos(df: pd.DataFrame) -> bool:
    if len(df) < 17:
        return False
    swing_high = df["high"].iloc[-17:-2].max()
    return df.iloc[-1]["close"] > swing_high

def detect_regime(df: pd.DataFrame) -> Regime:
    """
    15m 봉 기준 시장 국면 판단 (3단계 로직).
    1. ADX >= 25: 추세장 (UP/DOWN)
    2. ADX <= 22: 횡보장 (SIDEWAYS_UP/DOWN)
    3. 22 < ADX < 25: 관망 (DEADZONE) - 매매 쉬기
    """
    row    = df.iloc[-1]
    adx    = row["adx"]
    close  = row["close"]
    ema200 = row["ema200"]

    if pd.isna(adx) or pd.isna(ema200):
        return Regime.DEADZONE

    # [1] 강한 추세장 (ADX >= 25)
    if adx >= 25:
        ema50 = row["ema50"]
        if pd.isna(ema50): return Regime.DEADZONE
        
        if close > ema50:
            return Regime.UPTREND
        else:
            return Regime.DOWNTREND

    # [2] 완벽한 횡보장 (ADX <= 22)
    elif adx <= 22:
        if close > ema200:
            return Regime.SIDEWAYS_UP
        else:
            return Regime.SIDEWAYS_DOWN

    # [3] 데드존 (23 < ADX < 24): 매매 중단
    return Regime.DEADZONE
