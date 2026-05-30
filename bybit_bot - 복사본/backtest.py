import pandas as pd
import numpy as np
import time
import datetime as dt
from pybit.unified_trading import HTTP
import ta
import os
from dotenv import load_dotenv

# .env 파일 로드 (API 키 정보가 필요한 경우)
load_dotenv()

# --- [1. 백테스트 설정 및 상수] ---
SYMBOL = "BTCUSDT"
TIMEFRAME = "15"
LEVERAGE = 10
COMMISSION = 0.0006  # 0.06% (시장가 기준 왕복)
INITIAL_BALANCE = 10000.0

# 장세 판단 임계값 (요청 사항: ADX 22)
ADX_SIDEWAYS_THRESHOLD = 22

# 실시간 감시 파라미터 (현재 봇 로직 복제)
BE_TRIGGER_ROI = 3.0    # 3% ROI 시 본절 이동
TS_TRIGGER_ROI = 5.0    # 5% ROI 시 트레일링 시작
TS_DROP_PCT    = 0.015  # 고점 대비 1.5% 하락 시 익절

# 전략별 상수
EMA_LEN = 200
ADX_LEN = 14
ATR_LEN = 14
BB_LEN  = 20
BB_STD  = 2.0
RSI_LEN = 14
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 65
FVG_LOOKBACK = 50

# 익절/손절 배수
ATR_SL_MULT = 1.5
ATR_TP_MULT = 3.0

# API 설정
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")

def fetch_historical_data(symbol, interval, years=1):
    """바이비트 API에서 1년치 데이터를 가져옴."""
    session = HTTP(api_key=API_KEY, api_secret=API_SECRET)
    end_time = int(time.time() * 1000)
    start_time = end_time - (years * 365 * 24 * 60 * 60 * 1000)
    
    all_rows = []
    current_end = end_time
    
    print(f"\n[1/3] {symbol} 데이터 수집 시작 (약 {years}년)...")
    
    while current_end > start_time:
        try:
            resp = session.get_kline(
                category="linear", symbol=symbol, interval=interval,
                end=current_end, limit=1000
            )
            rows = resp.get("result", {}).get("list", [])
            if not rows: break
            all_rows.extend(rows)
            current_end = int(rows[-1][0]) - 1
            print(f"  > 수집 중... 총 {len(all_rows)}개 봉 완료", end="\r")
            if len(rows) < 1000: break
            time.sleep(0.05)
        except Exception as e:
            print(f"\n[Error] 데이터 수집 중 오류: {e}")
            break
            
    if not all_rows:
        print("\n[Error] 수집된 데이터가 없습니다. API 키를 확인하세요.")
        return None

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df = df.astype(float).sort_values("ts").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    print(f"\n[OK] 데이터 수집 완료. 기간: {df['ts'].iloc[0]} ~ {df['ts'].iloc[-1]}")
    return df

def add_indicators(df):
    print("[2/3] 지표 계산 중 (ADX, BB, RSI, ATR, EMA)...")
    h, l, c = df["high"], df["low"], df["close"]
    df["adx"] = ta.trend.ADXIndicator(h, l, c, ADX_LEN).adx()
    df["ema200"] = ta.trend.EMAIndicator(c, EMA_LEN).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(h, l, c, ATR_LEN).average_true_range()
    bb = ta.volatility.BollingerBands(c, BB_LEN, BB_STD)
    df["bb_u"], df["bb_l"] = bb.bollinger_hband(), bb.bollinger_lband()
    df["rsi"] = ta.momentum.RSIIndicator(c, RSI_LEN).rsi()
    return df

def run_backtest(df):
    print("[3/3] 매매 시뮬레이션 실행 중...")
    balance = INITIAL_BALANCE
    pos = 0; entry_p = 0; tp_p = 0; sl_p = 0; be_active = False; peak_p = 0; entry_regime = ""
    
    results = {
        "SIDEWAYS":  {"trades": 0, "wins": 0, "pnl": 0.0, "eq": [INITIAL_BALANCE]},
        "UPTREND":   {"trades": 0, "wins": 0, "pnl": 0.0, "eq": [INITIAL_BALANCE]},
        "DOWNTREND": {"trades": 0, "wins": 0, "pnl": 0.0, "eq": [INITIAL_BALANCE]}
    }
    
    total_len = len(df)
    for i in range(250, total_len):
        if i % 2000 == 0: print(f"  > 시뮬레이션 진행도: {i}/{total_len}", end="\r")
        
        row = df.iloc[i]; prev = df.iloc[i-1]
        
        # 실시간 장세 판단
        adx = row["adx"]
        current_regime = "SIDEWAYS" if adx < ADX_SIDEWAYS_THRESHOLD else ("UPTREND" if row["close"] > row["ema200"] else "DOWNTREND")
            
        if pos != 0:
            # ROI 계산
            roi = (row["close"]/entry_p - 1)*100*LEVERAGE if pos == 1 else (1 - row["close"]/entry_p)*100*LEVERAGE
            
            # 트레일링 고점 업데이트
            if roi >= TS_TRIGGER_ROI:
                peak_p = max(peak_p, row["high"]) if pos == 1 else min(peak_p, row["low"])
            
            closed = False; exit_p = 0
            # A. 기본 익절/손절
            if pos == 1:
                if row["low"] <= sl_p: closed = True; exit_p = sl_p
                elif row["high"] >= tp_p: closed = True; exit_p = tp_p
                elif roi >= TS_TRIGGER_ROI and row["close"] <= peak_p * (1 - TS_DROP_PCT): closed = True; exit_p = row["close"]
                elif entry_regime == "SIDEWAYS" and row["close"] >= row["bb_u"]: closed = True; exit_p = row["close"]
            else:
                if row["high"] <= sl_p: # Short SL (Wait, fixed logic)
                    pass 
                if row["high"] >= sl_p: closed = True; exit_p = sl_p
                elif row["low"] <= tp_p: closed = True; exit_p = tp_p
                elif roi >= TS_TRIGGER_ROI and row["close"] >= peak_p * (1 + TS_DROP_PCT): closed = True; exit_p = row["close"]
                elif entry_regime == "SIDEWAYS" and row["close"] <= row["bb_l"]: closed = True; exit_p = row["close"]

            # B. 본절 가드
            if not be_active and roi >= BE_TRIGGER_ROI:
                sl_p = entry_p; be_active = True

            if closed:
                # 수익률 및 수수료 반영
                pnl_pct = (exit_p/entry_p - 1)*LEVERAGE if pos == 1 else (1 - exit_p/entry_p)*LEVERAGE
                trade_pnl = balance * (pnl_pct - COMMISSION*2)
                balance += trade_pnl
                
                results[entry_regime]["trades"] += 1
                if trade_pnl > 0: results[entry_regime]["wins"] += 1
                results[entry_regime]["pnl"] += (trade_pnl / INITIAL_BALANCE) * 100
                results[entry_regime]["eq"].append(balance)
                
                pos = 0; be_active = False
        else:
            # 진입 로직 (봇과 100% 동일)
            if current_regime == "SIDEWAYS":
                if (row["close"] <= row["bb_l"] or prev["close"] <= prev["bb_l"]) and row["rsi"] <= RSI_OVERSOLD: pos = 1
                elif (row["close"] >= row["bb_u"] or prev["close"] >= prev["bb_u"]) and row["rsi"] >= RSI_OVERBOUGHT: pos = -1
            
            elif current_regime == "UPTREND":
                fvgs = df.iloc[i-FVG_LOOKBACK:i]
                for j in range(len(fvgs)-2):
                    if fvgs.iloc[j]["high"] < fvgs.iloc[j+2]["low"]:
                        top, bot = fvgs.iloc[j+2]["low"], fvgs.iloc[j]["high"]
                        mid = (top + bot) / 2
                        if mid <= row["close"] <= top: pos = 1; break
            
            elif current_regime == "DOWNTREND":
                fvgs = df.iloc[i-FVG_LOOKBACK:i]
                for j in range(len(fvgs)-2):
                    if fvgs.iloc[j]["low"] > fvgs.iloc[j+2]["high"]:
                        top, bot = fvgs.iloc[j]["low"], fvgs.iloc[j+2]["high"]
                        mid = (top + bot) / 2
                        if bot <= row["close"] <= mid: pos = -1; break

            if pos != 0:
                entry_p = row["close"]; peak_p = entry_p; entry_regime = current_regime
                atr = row["atr"]
                if pos == 1:
                    sl_p = entry_p - (atr * ATR_SL_MULT)
                    tp_p = entry_p + (atr * ATR_TP_MULT)
                else:
                    sl_p = entry_p + (atr * ATR_SL_MULT)
                    tp_p = entry_p - (atr * ATR_TP_MULT)
    
    return results

def print_report(res):
    print("\n" + "█"*30 + " [ 백테스트 최종 결과 리포트 ] " + "█"*30)
    print(f"{'장세 (MARKET REGIME)':<20} | {'진입횟수':<10} | {'승률':<10} | {'누적수익률':<12} | {'MDD':<10}")
    print("-" * 88)
    
    for r, d in res.items():
        cnt = d["trades"]
        wr = (d["wins"] / cnt * 100) if cnt > 0 else 0
        pnl = d["pnl"]
        
        # MDD 계산
        eq = np.array(d["eq"])
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak * 100
        mdd = np.min(dd) if len(dd) > 0 else 0
        
        print(f"{r:<20} | {cnt:<12} | {wr:>8.1f}% | {pnl:>12.2f}% | {mdd:>9.2f}%")
    print("█"*88)

if __name__ == "__main__":
    df = fetch_historical_data(SYMBOL, TIMEFRAME, 1)
    if df is not None:
        df = add_indicators(df)
        final_res = run_backtest(df)
        print_report(final_res)
    
    input("\n결과를 모두 확인하셨다면 엔터를 눌러 종료하세요...")
