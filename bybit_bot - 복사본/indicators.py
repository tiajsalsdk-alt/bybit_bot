import pandas as pd
import numpy as np
import ta
from config import (
    ADX_LEN, EMA_LEN, ATR_LEN, 
    BB_LEN, BB_STD, RSI_LEN, 
    VOL_SMA_LEN, STOCH_RSI_LEN, STOCH_K_LEN, STOCH_D_LEN, STOCH_SMOOTH
)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    [Quant Suite] 모든 전략(SMC, BB Scalper)을 위한 지표 통합 계산
    """
    df = df.copy()
    if df.empty:
        return df
        
    # 최소 데이터 개수 미달 시 빈 컬럼이라도 생성하여 KeyError 방지
    if len(df) < 50:
        for col in ['bw', 'd_low', 'd_high', 'stoch_k', 'stoch_d', 'ema20', 'ema50', 'ema200', 'adx', 'atr']:
            df[col] = np.nan
        return df
        
    h, l, c, v = df["high"], df["low"], df["close"], df["volume"]

    # 1. 공통 지표
    df["adx"]    = ta.trend.ADXIndicator(high=h, low=l, close=c, window=ADX_LEN).adx()
    df["ema200"] = ta.trend.EMAIndicator(close=c, window=EMA_LEN).ema_indicator()
    df["ema50"]  = ta.trend.EMAIndicator(close=c, window=50).ema_indicator()
    df["ema20"]  = ta.trend.EMAIndicator(close=c, window=20).ema_indicator()
    df["atr"]    = ta.volatility.AverageTrueRange(high=h, low=l, close=c, window=ATR_LEN).average_true_range()

    # 2. 볼린저 밴드 및 밴드폭 ('bw')
    bb = ta.volatility.BollingerBands(close=c, window=BB_LEN, window_dev=BB_STD)
    df["bb_up"] = bb.bollinger_hband()
    df["bb_dn"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()
    # [명세서 1] 밴드폭 'bw' 산출
    df["bw"] = (df["bb_up"] - df["bb_dn"]) / df["bb_mid"]
    df["bw_sma"] = df["bw"].rolling(window=20).mean()

    # 3. RSI 및 StochRSI (로직 2 전용)
    rsi_ind = ta.momentum.RSIIndicator(close=c, window=STOCH_RSI_LEN)
    df["rsi"] = rsi_ind.rsi()
    
    # StochRSI 직접 계산 (ta 라이브러리 호환성 확보 및 NaN 방어)
    rsi = df["rsi"]
    rsi_min = rsi.rolling(window=STOCH_K_LEN).min()
    rsi_max = rsi.rolling(window=STOCH_K_LEN).max()
    
    # [V3.5 방어] 분모가 0인 경우(rsi_max == rsi_min) 처리 및 NaN 제거
    denom = rsi_max - rsi_min
    stoch_rsi = ((rsi - rsi_min) / denom * 100).fillna(0)
    
    df["stoch_k"] = stoch_rsi.rolling(window=STOCH_SMOOTH).mean().fillna(0)
    df["stoch_d"] = df["stoch_k"].rolling(window=STOCH_D_LEN).mean().fillna(0)

    # 4. 거래량 이평선
    df["vol_sma"] = v.rolling(window=VOL_SMA_LEN).mean()

    # 5. [명세서 2] 유동성 스윕선 'd_low', 'd_high' (15캔들 고저점, shift 1)
    df["d_low"] = l.rolling(window=15).min().shift(1)
    df["d_high"] = h.rolling(window=15).max().shift(1)

    return df
