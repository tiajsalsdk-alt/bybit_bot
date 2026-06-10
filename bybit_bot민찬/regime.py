import pandas as pd
from enum import Enum
from config import ADX_TREND, ADX_SIDEWAYS

class Regime(Enum):
    SIDEWAYS_UP   = "SIDEWAYS_UP"   # 거시적 상승 중 횡보 (Long 전용)
    SIDEWAYS_DOWN = "SIDEWAYS_DOWN" # 거시적 하락 중 횡보 (Short 전용)
    UPTREND       = "UPTREND"       # 강한 상승 추세
    DOWNTREND     = "DOWNTREND"     # 강한 하락 추세
    DEADZONE      = "DEADZONE"      # 관망 구간

def detect_regime(df: pd.DataFrame) -> Regime:
    """
    [Bug Fix 4] 하드코딩 제거 및 설정 변수 연동
    15m 봉 기준 시장 국면 판단.
    1. ADX >= ADX_TREND: 추세장 (UP/DOWN)
    2. ADX <= ADX_SIDEWAYS: 횡보장 (SIDEWAYS_UP/DOWN)
    3. 그 외: 관망 (DEADZONE)
    """
    row    = df.iloc[-1]
    adx    = row["adx"]
    close  = row["close"]
    ema200 = row["ema200"]

    if pd.isna(adx) or pd.isna(ema200):
        return Regime.DEADZONE

    # [1] 강한 추세장 (설정된 ADX_TREND 사용)
    if adx >= ADX_TREND:
        ema50 = row["ema50"]
        if pd.isna(ema50): return Regime.DEADZONE
        
        if close > ema50:
            return Regime.UPTREND
        else:
            return Regime.DOWNTREND

    # [2] 완벽한 횡보장 (설정된 ADX_SIDEWAYS 사용)
    elif adx <= ADX_SIDEWAYS:
        if close > ema200:
            return Regime.SIDEWAYS_UP
        else:
            return Regime.SIDEWAYS_DOWN

    # [3] 데드존
    return Regime.DEADZONE
