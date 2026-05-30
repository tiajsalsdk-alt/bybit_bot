import os
import pandas as pd
import numpy as np
import ta
from config import (
    VOL_MULT_SMC, VOL_MULT_PULLBACK, BB_LEN, BB_STD,
    STOCH_RSI_LEN, STOCH_K_LEN, STOCH_D_LEN, STOCH_SMOOTH,
    STOCH_PRO_LOW, STOCH_PRO_HIGH, STOCH_ANTI_LOW, STOCH_ANTI_HIGH,
    ADX_TREND, ADX_SIDEWAYS
)

import logging
log = logging.getLogger(__name__)

# ANSI Color Codes
os.system('') # Enable ANSI colors on Windows
COLOR_YELLOW = '\033[93m'
COLOR_RED = '\033[91m'
COLOR_CYAN = '\033[96m'
COLOR_RESET = '\033[0m'

def get_signal(df: pd.DataFrame, htf_adx: float, htf_ema: float, symbol: str = "Unknown") -> tuple[str, str] | None:
    """
    [Bug Fix 3] ADX 임계값 설정 연동 및 데드존 정합성 수정
    htf_adx >= ADX_TREND: 로직 1 (SMC Sniper + 얕은 눌림목)
    htf_adx <= ADX_SIDEWAYS: 로직 2 (볼벤 + StochRSI 비대칭 하이브리드)
    """
    if len(df) < 50: return None
    
    # [공통] 지표 계산
    df['vol_sma'] = df['volume'].rolling(window=20).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    
    cur = df.iloc[-1]
    prev = df.iloc[-2]
    
    # --- [로직 1: SMC Sniper + 얕은 눌림목 (추세장)] ---
    if htf_adx >= ADX_TREND:
        # 1-A. SMC 스윕 타점
        df['d_low'] = df['low'].rolling(window=15).min().shift(1)
        df['d_high'] = df['high'].rolling(window=15).max().shift(1)
        
        smc_long = (cur['low'] < cur['d_low']) and (cur['close'] > cur['d_low']) and (cur['volume'] > cur['vol_sma'] * VOL_MULT_SMC)
        smc_short = (cur['high'] > cur['d_high']) and (cur['close'] < cur['d_high']) and (cur['volume'] > cur['vol_sma'] * VOL_MULT_SMC)
        
        # 1-B. 얕은 눌림목 (Shallow Pullback) 타점
        is_uptrend = cur['close'] > htf_ema
        pullback_long = is_uptrend and (cur['low'] <= cur['ema20']) and (cur['close'] > cur['ema20']) and (cur['volume'] > cur['vol_sma'] * VOL_MULT_PULLBACK)
        pullback_short = (not is_uptrend) and (cur['high'] >= cur['ema20']) and (cur['close'] < cur['ema20']) and (cur['volume'] > cur['vol_sma'] * VOL_MULT_PULLBACK)

        if smc_long or pullback_long: return "Buy", "SMC_SNIPER"
        if smc_short or pullback_short: return "Sell", "SMC_SNIPER"
        
        # [진단 로그]
        if not (smc_long or smc_short or pullback_long or pullback_short):
            reason = ""
            if is_uptrend and cur['close'] > cur['ema20']: reason = "추세 정배열이나 눌림(EMA20) 부족"
            elif not is_uptrend and cur['close'] < cur['ema20']: reason = "추세 역배열이나 반등(EMA20) 부족"
            else: reason = "SMC 스윕 및 거래량 조건 미달"
            print(f"[Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - {reason}")

    # --- [로직 2: 볼벤 + StochRSI (횡보장)] ---
    elif htf_adx <= ADX_SIDEWAYS:
        ma = df['close'].rolling(window=20).mean()
        std = df['close'].rolling(window=20).std()
        df['bb_up'] = ma + (std * BB_STD)
        df['bb_dn'] = ma - (std * BB_STD)
        
        if pd.isna(cur['stoch_d']): 
            print(f"[Skip] {COLOR_YELLOW}{symbol}{COLOR_RESET} - StochRSI 데이터 계산 중 (NaN)")
            return None

        is_uptrend = cur['close'] > htf_ema

        # [LONG 타점]
        bb_reclaim_long = (cur['low'] < df['bb_dn'].iloc[-1]) and (cur['close'] > df['bb_dn'].iloc[-1])
        if bb_reclaim_long:
            if is_uptrend:
                if (cur['stoch_k'] <= STOCH_PRO_LOW) and (prev['stoch_k'] <= prev['stoch_d'] and cur['stoch_k'] > cur['stoch_d']):
                    return "Buy", "SIDEWAYS_PRO"
                else:
                    print(f"[Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - LONG(순) StochRSI 조건 미달")
            else:
                if (cur['stoch_k'] <= STOCH_ANTI_LOW) and (prev['stoch_k'] <= prev['stoch_d'] and cur['stoch_k'] > cur['stoch_d']):
                    return "Buy", "SIDEWAYS_ANTI"
                else:
                    print(f"[Skip] {COLOR_CYAN}{symbol}{COLOR_RESET} - LONG(역) StochRSI 조건 미달")
        
        # [SHORT 타점]
        bb_reclaim_short = (cur['high'] > df['bb_up'].iloc[-1]) and (cur['close'] < df['bb_up'].iloc[-1])
        if bb_reclaim_short:
            if not is_uptrend:
                if (cur['stoch_k'] >= STOCH_PRO_HIGH) and (prev['stoch_k'] >= prev['stoch_d'] and cur['stoch_k'] < cur['stoch_d']):
                    return "Sell", "SIDEWAYS_PRO"
                else:
                    print(f"[Skip] {COLOR_YELLOW}{symbol}{COLOR_RESET} - SHORT(순) StochRSI 조건 미달")
            else:
                if (cur['stoch_k'] >= STOCH_ANTI_HIGH) and (prev['stoch_k'] >= prev['stoch_d'] and cur['stoch_k'] < cur['stoch_d']):
                    return "Sell", "SIDEWAYS_ANTI"
                else:
                    print(f"[Skip] {COLOR_YELLOW}{symbol}{COLOR_RESET} - SHORT(역) StochRSI 조건 미달")
        
        if not (bb_reclaim_long or bb_reclaim_short):
            print(f"[Skip] {COLOR_YELLOW}{symbol}{COLOR_RESET} - 볼린저 밴드 이탈 후 복귀(Reclaim) 실패")
    
    else:
        # [Bug Fix 3] 데드존 로그 업데이트
        print(f"[Skip] {COLOR_YELLOW}{symbol}{COLOR_RESET} - ADX {htf_adx:.1f} (데드존 {ADX_SIDEWAYS}~{ADX_TREND})")
            
    return None
