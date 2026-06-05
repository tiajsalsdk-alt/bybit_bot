import os
import pandas as pd
import numpy as np
import ta
from config import (
    FVG_VOL_MULT, ADX_TREND_LEVEL, ADX_SIDEWAYS_LEVEL,
    BB_LEN, BB_STD, STOCH_PRO_LOW, STOCH_PRO_HIGH,
    STOCH_RSI_K_LIMIT_LONG, STOCH_RSI_K_LIMIT_SHORT,
    FVG_RISK_CAP_ATR_MULT, FVG_SL_ATR_BUFFER
)

import logging
log = logging.getLogger(__name__)

# ANSI Color Codes
os.system('') 
COLOR_YELLOW = '\033[93m'
COLOR_RED = '\033[91m'
COLOR_CYAN = '\033[96m'
COLOR_RESET = '\033[0m'

def get_signal(df_1h: pd.DataFrame, df_entry: pd.DataFrame, symbol: str, htf_ema: float) -> tuple[str, str, float, float, float] | None:
    """
    [V3.2 Optimized Engine]
    - FVG StochRSI Filter: Long < 80, Short > 20
    - Structural SL: 1번 캔들 Low/High +/- 0.5 ATR
    - Risk Cap: SL Distance <= 2.5 ATR
    """
    if len(df_1h) < 50 or len(df_entry) < 50: return None
    
    adx_1h = df_1h.iloc[-1]["adx"]
    curr_close = df_entry.iloc[-1]["close"]
    stoch_k = df_entry.iloc[-1]["stoch_k"]
    atr = df_entry.iloc[-1]["atr"]
    
    df_entry['ma20'] = df_entry['close'].rolling(window=20).mean()
    curr_ma20 = df_entry['ma20'].iloc[-1]
    
    # --- [1] 추세장 (1H ADX >= 25): 5분봉 FVG ---
    if adx_1h >= ADX_TREND_LEVEL:
        if symbol == "BTCUSDT":
            print(f"  [Pass] {symbol} - 1H 추세장 BTC 매매 제외")
            return None
        
        is_long_trend = curr_close > htf_ema
        
        c1, c2, c3 = df_entry.iloc[-3], df_entry.iloc[-2], df_entry.iloc[-1]
        v1, v2 = df_entry['volume'].iloc[-3], df_entry['volume'].iloc[-2]
        
        if v2 < v1 * FVG_VOL_MULT:
            print(f"  [Pass] {symbol} - 5m FVG 거래량 미달 ({v2/v1:.2f}x)")
            return None
            
        # Bullish FVG (Long)
        if c3['low'] > c1['high']:
            if is_long_trend:
                # 조건 1: 단기 과열 필터
                if stoch_k >= STOCH_RSI_K_LIMIT_LONG:
                    print(f"  [Pass] {symbol} - StochRSI 과열 ({stoch_k:.1f} >= {STOCH_RSI_K_LIMIT_LONG})")
                    return None
                
                # 조건 2: CE 50% 타점
                ce_price = (c1['high'] + c3['low']) / 2
                
                # 조건 3: 구조적 손절 및 리스크 캡
                sl_price = c1['low'] - (FVG_SL_ATR_BUFFER * atr)
                if abs(ce_price - sl_price) > (FVG_RISK_CAP_ATR_MULT * atr):
                    print(f"  [Pass] {symbol} - 리스크 캡 초과 ({(abs(ce_price-sl_price)/atr):.2f} ATR)")
                    return None
                
                return "Buy", "TREND_FVG", ce_price, sl_price, 0.0
            return None
            
        # Bearish FVG (Short)
        elif c3['high'] < c1['low']:
            if not is_long_trend:
                # 조건 1: 단기 과열 필터
                if stoch_k <= STOCH_RSI_K_LIMIT_SHORT:
                    print(f"  [Pass] {symbol} - StochRSI 과매도 ({stoch_k:.1f} <= {STOCH_RSI_K_LIMIT_SHORT})")
                    return None
                
                # 조건 2: 100% Full Fill 타점 (Deep Discount)
                ce_price = c1['low']
                
                # 조건 3: 구조적 손절 및 리스크 캡
                sl_price = c1['high'] + (FVG_SL_ATR_BUFFER * atr)
                if abs(ce_price - sl_price) > (FVG_RISK_CAP_ATR_MULT * atr):
                    print(f"  [Pass] {symbol} - 리스크 캡 초과 ({(abs(ce_price-sl_price)/atr):.2f} ATR)")
                    return None
                
                return "Sell", "TREND_FVG", ce_price, sl_price, 0.0
            return None


    # --- [2] 횡보장 및 데드존 (1H ADX < 25): 15분봉 BB ---
    else:
        # 데드존(23~24) 전용 로그 출력
        if ADX_SIDEWAYS_LEVEL < adx_1h < ADX_TREND_LEVEL:
            print(f"[장세 필터] {symbol} - ADX {adx_1h:.1f}: 추세(FVG) 차단, 횡보장 로직 가동")

        ma = df_entry['ma20']
        std = df_entry['close'].rolling(window=20).std()
        df_entry['bb_up'] = ma + (std * 1.5)
        df_entry['bb_dn'] = ma - (std * 1.5)
        
        if pd.isna(df_entry.iloc[-1]['stoch_d']): return None
        prev_entry = df_entry.iloc[-2]
        cur_entry = df_entry.iloc[-1]
        
        is_uptrend_htf = curr_close > htf_ema

        # [1. LONG 타점: 하단 터치(Low <= BB_DN) 및 과매도(Stoch K <= 30)]
        if (cur_entry['low'] <= df_entry['bb_dn'].iloc[-1]) and (cur_entry['stoch_k'] <= STOCH_PRO_LOW):
            engine = "SIDEWAYS_PRO" if is_uptrend_htf else "SIDEWAYS_ANTI"
            return "Buy", engine, curr_close, 0.0, curr_ma20
        
        # [2. SHORT 타점: 상단 터치(High >= BB_UP) 및 과매수(Stoch K >= 70)]
        if (cur_entry['high'] >= df_entry['bb_up'].iloc[-1]) and (cur_entry['stoch_k'] >= STOCH_PRO_HIGH):
            engine = "SIDEWAYS_PRO" if not is_uptrend_htf else "SIDEWAYS_ANTI"
            return "Sell", engine, curr_close, 0.0, curr_ma20
        
        # 횡보 타점 미달 시 로그 (데드존 외 순수 횡보장일 때만)
        if adx_1h <= ADX_SIDEWAYS_LEVEL:
            print(f"  [Pass] {symbol} - 15m 횡보 타점 미달 (1H ADX:{adx_1h:.1f})")
        
    return None

