import os
import pandas as pd
import numpy as np
import ta
from config import (
    FVG_VOL_MULT, ADX_TREND_LEVEL, ADX_SIDEWAYS_LEVEL,
    BB_LEN, BB_STD, STOCH_PRO_LOW, STOCH_PRO_HIGH, STOCH_ANTI_LOW, STOCH_ANTI_HIGH
)

import logging
log = logging.getLogger(__name__)

# ANSI Color Codes
os.system('') # Enable ANSI colors on Windows
COLOR_YELLOW = '\033[93m'
COLOR_RED = '\033[91m'
COLOR_CYAN = '\033[96m'
COLOR_RESET = '\033[0m'

def get_signal(df_regime: pd.DataFrame, df_entry: pd.DataFrame, symbol: str, htf_ema: float) -> tuple[str, str, float, float] | None:
    """
    [MTF Hybrid Engine V3]
    - df_regime: 1시간봉 (1H) -> ADX로 전체 장세 판별
    - df_entry: 타점봉 (1H ADX >= 25 ? 5m : 15m)
    """
    if len(df_regime) < 50 or len(df_entry) < 50: return None
    
    # 1. 1시간봉 ADX로 장세 판별
    cur_regime = df_regime.iloc[-1]
    adx_1h = cur_regime["adx"]
    
    cur_entry = df_entry.iloc[-1]
    prev_entry = df_entry.iloc[-2]
    curr_close = cur_entry["close"]
    
    # [공통] 지표 계산 (타점봉 기준)
    df_entry['ma20'] = df_entry['close'].rolling(window=20).mean()
    curr_ma20 = df_entry['ma20'].iloc[-1]
    
    # --- [A] 1H 추세장 (ADX >= 25): 5분봉 FVG 순추세 ---
    if adx_1h >= ADX_TREND_LEVEL:
        if symbol == "BTCUSDT":
            print(f"  [Pass] {symbol} - 1H 추세장 BTC 제외")
            return None
        
        # 1H EMA 50 대추세 필터 (현재가 vs 1H EMA)
        is_long_trend = curr_close > htf_ema
        
        # 5m FVG 탐지 (최근 3캔들)
        c1, c2, c3 = df_entry.iloc[-3], df_entry.iloc[-2], df_entry.iloc[-1]
        v1, v2 = df_entry['volume'].iloc[-3], df_entry['volume'].iloc[-2]
        
        if v2 < v1 * FVG_VOL_MULT:
            print(f"  [Pass] {symbol} - 5m FVG 거래량 미달 ({v2/v1:.2f}x)")
            return None
            
        if c3['low'] > c1['high']:
            if is_long_trend: return "Buy", "TREND_FVG", c1['high'], 0.0
            print(f"  [Pass] {symbol} - 1H EMA 역추세 (EMA:{htf_ema:.2f})")
        elif c3['high'] < c1['low']:
            if not is_long_trend: return "Sell", "TREND_FVG", c1['low'], 0.0
            print(f"  [Pass] {symbol} - 1H EMA 역추세 (EMA:{htf_ema:.2f})")
        else:
            print(f"  [Pass] {symbol} - 5m FVG 구조 미발생")

    # --- [B] 1H 횡보장 (ADX < 25): 15분봉 BB 역추세 ---
    else:
        # [주의] 이 시점의 df_entry는 15분봉임이 보장됨
        ma = df_entry['ma20']
        std = df_entry['close'].rolling(window=BB_LEN).std()
        df_entry['bb_up'] = ma + (std * BB_STD)
        df_entry['bb_dn'] = ma - (std * BB_STD)
        
        if pd.isna(cur_entry['stoch_d']): return None
        is_uptrend_htf = curr_close > htf_ema

        # [LONG 타점]
        if (cur_entry['low'] < df_entry['bb_dn'].iloc[-1]) and (curr_close > df_entry['bb_dn'].iloc[-1]):
            if is_uptrend_htf and (cur_entry['stoch_k'] <= STOCH_PRO_LOW) and (prev_entry['stoch_k'] <= prev_entry['stoch_d'] and cur_entry['stoch_k'] > cur_entry['stoch_d']):
                return "Buy", "SIDEWAYS_PRO", curr_close, curr_ma20
            elif not is_uptrend_htf and (cur_entry['stoch_k'] <= STOCH_ANTI_LOW) and (prev_entry['stoch_k'] <= prev_entry['stoch_d'] and cur_entry['stoch_k'] > cur_entry['stoch_d']):
                return "Buy", "SIDEWAYS_ANTI", curr_close, curr_ma20
        
        # [SHORT 타점]
        if (cur_entry['high'] > df_entry['bb_up'].iloc[-1]) and (curr_close < df_entry['bb_up'].iloc[-1]):
            if not is_uptrend_htf and (cur_entry['stoch_k'] >= STOCH_PRO_HIGH) and (prev_entry['stoch_k'] >= prev_entry['stoch_d'] and cur_entry['stoch_k'] < cur_entry['stoch_d']):
                return "Sell", "SIDEWAYS_PRO", curr_close, curr_ma20
            elif is_uptrend_htf and (cur_entry['stoch_k'] >= STOCH_ANTI_HIGH) and (prev_entry['stoch_k'] >= prev_entry['stoch_d'] and cur_entry['stoch_k'] < cur_entry['stoch_d']):
                return "Sell", "SIDEWAYS_ANTI", curr_close, curr_ma20
        
        print(f"  [Pass] {symbol} - 15m 횡보 타점 미달 (1H ADX:{adx_1h:.1f})")
        
    return None
