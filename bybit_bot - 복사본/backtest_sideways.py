import pandas as pd
import numpy as np
from indicators import add_indicators
from strategies import signal_sideways
from market_data import fetch_candles

# 백테스팅 설정
INITIAL_BALANCE = 1000
COMMISSION = 0.0006
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
LIMIT = 2000  # 더 정확한 통계를 위해 2000개 캔들 사용

def run_sideways_backtest(symbol):
    print(f"[{symbol}] Sideways data fetching...")
    df = fetch_candles(symbol, "15", limit=LIMIT)
    df = add_indicators(df)
    
    balance = INITIAL_BALANCE
    position = 0  # 1: Long, -1: Short
    entry_price = 0
    tp_price = 0
    sl_price = 0
    
    equity_curve = [INITIAL_BALANCE]
    trades = []

    for i in range(50, len(df)):
        curr_price = df.iloc[i]['close']
        
        if position == 0:
            # 오직 횡보장 전략(BB + RSI)만 사용
            signal = signal_sideways(df.iloc[:i+1])
            if signal == "Buy":
                position = 1
                entry_price = curr_price
                atr = df.iloc[i]['atr']
                sl_price = entry_price - (atr * 1.5)
                tp_price = entry_price + (atr * 3.0)
                balance -= balance * COMMISSION
            elif signal == "Sell":
                position = -1
                entry_price = curr_price
                atr = df.iloc[i]['atr']
                sl_price = entry_price + (atr * 1.5)
                tp_price = entry_price - (atr * 3.0)
                balance -= balance * COMMISSION
        
        else:
            low = df.iloc[i]['low']
            high = df.iloc[i]['high']
            closed = False
            pnl = 0
            
            if position == 1:
                if low <= sl_price:
                    pnl = (sl_price - entry_price) / entry_price
                    closed = True
                elif high >= tp_price:
                    pnl = (tp_price - entry_price) / entry_price
                    closed = True
            elif position == -1:
                if high >= sl_price:
                    pnl = (entry_price - sl_price) / entry_price
                    closed = True
                elif low <= tp_price:
                    pnl = (entry_price - tp_price) / entry_price
                    closed = True
            
            if closed:
                balance *= (1 + pnl)
                balance -= balance * COMMISSION
                trades.append(pnl)
                position = 0

        equity_curve.append(balance)

    return equity_curve, trades

def analyze(symbol, equity_curve, trades):
    equity_curve = np.array(equity_curve)
    roi = (equity_curve[-1] - INITIAL_BALANCE) / INITIAL_BALANCE * 100
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / peak * 100
    mdd = np.min(drawdown)
    win_rate = (len([t for t in trades if t > 0]) / len(trades) * 100) if trades else 0
    
    print(f"--- {symbol} Sideways Results ---")
    print(f"ROI: {roi:.2f}%")
    print(f"Win Rate: {win_rate:.2f}% ({len(trades)} trades)")
    print(f"MDD: {mdd:.2f}%")
    print(f"Final: {equity_curve[-1]:.2f}")

if __name__ == "__main__":
    for s in SYMBOLS:
        try:
            curve, trades = run_sideways_backtest(s)
            analyze(s, curve, trades)
        except Exception as e:
            print(f"Error {s}: {e}")
