import pandas as pd
from enum import Enum
from config import ADX_TREND_LEVEL, ADX_SIDEWAYS_LEVEL

class Regime(Enum):
    SIDEWAYS_UP   = "SIDEWAYS_UP"   # 거시적 상승 중 횡보 (Long 전용)
    SIDEWAYS_DOWN = "SIDEWAYS_DOWN" # 거시적 하락 중 횡보 (Short 전용)
    UPTREND       = "UPTREND"       # 강한 상승 추세
    DOWNTREND     = "DOWNTREND"     # 강한 하락 추세
    DEADZONE      = "DEADZONE"      # 관망 구간

def _has_bullish_bos(df: pd.DataFrame) -> bool:
    if len(df) < 17:
        return False
    swing_high = df["high"].iloc[-17:-2].max()
    return df.iloc[-1]["close"] > swing_high

def detect_regime(df: pd.DataFrame) -> Regime:
    """
    1H ADX 기준 시장 국면 판단.
    1. ADX >= 25: 추세장 (UP/DOWN)
    2. ADX < 25: 횡보장 (SIDEWAYS_UP/DOWN)
    """
    row    = df.iloc[-1]
    adx    = row["adx"]
    close  = row["close"]
    ema200 = row.get("ema200", close) # EMA 200이 없을 경우 현재가 사용

    if pd.isna(adx):
        return Regime.DEADZONE

    # [1] 강한 추세장
    if adx >= ADX_TREND_LEVEL:
        ema50 = row.get("ema50", close)
        if close > ema50:
            return Regime.UPTREND
        else:
            return Regime.DOWNTREND

    # [2] 완벽한 횡보장
    else:
        if close > ema200:
            return Regime.SIDEWAYS_UP
        else:
            return Regime.SIDEWAYS_DOWN
