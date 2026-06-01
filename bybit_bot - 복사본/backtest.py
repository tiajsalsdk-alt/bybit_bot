import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import ta
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

# --- [0] 실전 백테스트 파라미터 (V3.3 Master Full Hybrid) ---
# 종목 확대: 실전 봇의 스캐너 범위를 모사
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT", "MATIC/USDT", "LINK/USDT", "AVAX/USDT", "DOT/USDT"]
LEVERAGE = 10
POSITION_SIZE = 0.15 # 회당 자산 15% 투입
FEE_SLIPPAGE = 0.001  # 왕복 수수료 + 슬리피지 (0.1%)
INITIAL_BALANCE = 10000.0

# 전략 상수
ADX_TREND_LEVEL = 25
STOCH_RSI_LONG_LIMIT = 80
STOCH_RSI_SHORT_LIMIT = 20
STOCH_PRO_LOW = 25
STOCH_PRO_HIGH = 75
FVG_SL_ATR_BUFFER = 0.5
FVG_RISK_CAP_MULT = 2.5
TP1_R_MULT = 1.2
TP2_R_MULT = 2.0
FEE_BUFFER = 0.0015 # 본절 이동 시 수수료 마진

class FullHybridBacktester:
    def __init__(self):
        self.exchange = ccxt.bybit()
        self.balance = INITIAL_BALANCE
        self.trades = []
        self.total_trades_count = 0

    def fetch_ohlcv_paginated(self, symbol, timeframe, months=6):
        since = self.exchange.parse8601((datetime.utcnow() - timedelta(days=months*30)).isoformat())
        all_ohlcv = []
        limit = 1000
        
        while since < self.exchange.milliseconds():
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since, limit)
                if not ohlcv: break
                since = ohlcv[-1][0] + 1
                all_ohlcv.extend(ohlcv)
                if len(ohlcv) < limit: break
            except: break
        
        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df.set_index('datetime')

    def add_indicators(self, df):
        df = df.copy()
        if df.empty: return df
        df['adx'] = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14).adx()
        df['atr'] = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
        df['ema50'] = ta.trend.EMAIndicator(df['close'], window=50).ema_indicator()
        
        # StochRSI
        rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
        stoch_rsi = (rsi - rsi.rolling(14).min()) / (rsi.rolling(14).max() - rsi.rolling(14).min()) * 100
        df['stoch_k'] = stoch_rsi.rolling(3).mean()
        df['stoch_d'] = df['stoch_k'].rolling(3).mean()
        
        # Bollinger Bands
        bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=1.5)
        df['bb_up'], df['bb_dn'], df['bb_mid'] = bb.bollinger_hband(), bb.bollinger_lband(), bb.bollinger_mavg()
        return df

    def run_simulation(self, symbol):
        log.info(f"[*] {symbol} 데이터 로드 및 지표 계산 중...")
        df_5m = self.add_indicators(self.fetch_ohlcv_paginated(symbol, '5m'))
        if df_5m.empty: return
        
        # 15m, 1h 데이터 리샘플링 (미래 데이터 참조 방지)
        df_15m = self.add_indicators(df_5m.resample('15min').last().ffill())
        df_1h = self.add_indicators(df_5m.resample('60min').last().ffill())
        
        # 인덱스 동기화
        df_5m['adx_1h'] = df_1h['adx'].reindex(df_5m.index, method='ffill')
        df_5m['ema50_1h'] = df_1h['ema50'].reindex(df_5m.index, method='ffill')
        
        position = None
        
        for i in range(20, len(df_5m)):
            curr_5m = df_5m.iloc[i]
            ts = df_5m.index[i]
            
            # 포지션 관리
            if position:
                high, low, close = curr_5m['high'], curr_5m['low'], curr_5m['close']
                
                if position['engine'] == "TREND_FVG":
                    # 1차 TP (1.2R)
                    if not position['tp1_hit']:
                        if position['side'] == 'Buy' and high >= position['tp1']:
                            position['tp1_hit'] = True
                            position['sl'] = position['entry']
                            pnl_half = (position['qty'] * 0.5) * (position['tp1'] - position['entry'])
                            self.balance += pnl_half - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * 0.5)
                        elif position['side'] == 'Sell' and low <= position['tp1']:
                            position['tp1_hit'] = True
                            position['sl'] = position['entry']
                            pnl_half = (position['qty'] * 0.5) * (position['entry'] - position['tp1'])
                            self.balance += pnl_half - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * 0.5)
                    
                    # 최종 청산
                    if position['side'] == 'Buy':
                        if low <= position['sl']:
                            qty_left = position['qty'] * (0.5 if position['tp1_hit'] else 1.0)
                            pnl = qty_left * (position['sl'] - position['entry'])
                            self.balance += pnl - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * (0.5 if position['tp1_hit'] else 1.0))
                            self.trades.append(pnl); position = None; self.total_trades_count += 1
                        elif high >= position['tp2']:
                            qty_left = position['qty'] * (0.5 if position['tp1_hit'] else 1.0)
                            pnl = qty_left * (position['tp2'] - position['entry'])
                            self.balance += pnl - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * (0.5 if position['tp1_hit'] else 1.0))
                            self.trades.append(pnl); position = None; self.total_trades_count += 1
                    else: # Sell
                        if high >= position['sl']:
                            qty_left = position['qty'] * (0.5 if position['tp1_hit'] else 1.0)
                            pnl = qty_left * (position['entry'] - position['sl'])
                            self.balance += pnl - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * (0.5 if position['tp1_hit'] else 1.0))
                            self.trades.append(pnl); position = None; self.total_trades_count += 1
                        elif low <= position['tp2']:
                            qty_left = position['qty'] * (0.5 if position['tp1_hit'] else 1.0)
                            pnl = qty_left * (position['entry'] - position['tp2'])
                            self.balance += pnl - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * (0.5 if position['tp1_hit'] else 1.0))
                            self.trades.append(pnl); position = None; self.total_trades_count += 1
                
                elif "SIDEWAYS" in position['engine']:
                    # 횡보장 익절 (MA20 반익절, BB 반대편 완청)
                    curr_15m = df_15m.asof(ts)
                    ma20 = curr_15m['bb_mid']
                    bb_target = curr_15m['bb_up'] if position['side'] == 'Buy' else curr_15m['bb_dn']
                    
                    if not position['tp1_hit']:
                        hit_ma20 = (close >= ma20) if position['side'] == 'Buy' else (close <= ma20)
                        if hit_ma20:
                            position['tp1_hit'] = True
                            position['sl'] = position['entry'] * (1 + FEE_BUFFER if position['side'] == 'Buy' else 1 - FEE_BUFFER)
                            pnl_half = (position['qty'] * 0.5) * (close - position['entry'] if position['side'] == 'Buy' else position['entry'] - close)
                            self.balance += pnl_half - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * 0.5)
                    
                    if position['side'] == 'Buy':
                        if low <= position['sl']:
                            pnl = (position['qty'] * 0.5) * (position['sl'] - position['entry'])
                            self.balance += pnl - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * 0.5)
                            self.trades.append(pnl); position = None; self.total_trades_count += 1
                        elif high >= bb_target:
                            pnl = (position['qty'] * (0.5 if position['tp1_hit'] else 1.0)) * (bb_target - position['entry'])
                            self.balance += pnl - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * (0.5 if position['tp1_hit'] else 1.0))
                            self.trades.append(pnl); position = None; self.total_trades_count += 1
                    else: # Sell
                        if high >= position['sl']:
                            pnl = (position['qty'] * 0.5) * (position['entry'] - position['sl'])
                            self.balance += pnl - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * 0.5)
                            self.trades.append(pnl); position = None; self.total_trades_count += 1
                        elif low <= bb_target:
                            pnl = (position['qty'] * (0.5 if position['tp1_hit'] else 1.0)) * (position['entry'] - bb_target)
                            self.balance += pnl - (self.balance * POSITION_SIZE * FEE_SLIPPAGE * (0.5 if position['tp1_hit'] else 1.0))
                            self.trades.append(pnl); position = None; self.total_trades_count += 1
                continue

            # 진입 로직
            adx_1h = curr_5m['adx_1h']
            ema_1h = curr_5m['ema50_1h']
            
            # [1] 추세장 FVG (ADX >= 25)
            if adx_1h >= ADX_TREND_LEVEL:
                c1, c2, c3 = df_5m.iloc[i-2], df_5m.iloc[i-1], df_5m.iloc[i]
                if c3['low'] > c1['high'] and c3['close'] > ema_1h and c3['stoch_k'] < STOCH_RSI_LONG_LIMIT:
                    entry = c1['high']
                    sl = c1['low'] - (FVG_SL_ATR_BUFFER * c3['atr'])
                    risk = entry - sl
                    if risk > 0 and risk <= FVG_RISK_CAP_MULT * c3['atr']:
                        position = {'side': 'Buy', 'entry': entry, 'sl': sl, 'tp1': entry + (risk * TP1_R_MULT), 'tp2': entry + (risk * TP2_R_MULT), 'qty': (self.balance * POSITION_SIZE * LEVERAGE) / entry, 'tp1_hit': False, 'engine': 'TREND_FVG'}
                elif c3['high'] < c1['low'] and c3['close'] < ema_1h and c3['stoch_k'] > STOCH_RSI_SHORT_LIMIT:
                    entry = c1['low']
                    sl = c1['high'] + (FVG_SL_ATR_BUFFER * c3['atr'])
                    risk = sl - entry
                    if risk > 0 and risk <= FVG_RISK_CAP_MULT * c3['atr']:
                        position = {'side': 'Sell', 'entry': entry, 'sl': sl, 'tp1': entry - (risk * TP1_R_MULT), 'tp2': entry - (risk * TP2_R_MULT), 'qty': (self.balance * POSITION_SIZE * LEVERAGE) / entry, 'tp1_hit': False, 'engine': 'TREND_FVG'}

            # [2] 횡보장 BB (ADX < 25)
            else:
                curr_15m = df_15m.asof(ts)
                prev_15m = df_15m.iloc[df_15m.index.get_indexer([ts], method='ffill')[0]-1]
                
                # Long Reclaim
                if curr_5m['low'] < curr_15m['bb_dn'] and curr_5m['close'] > curr_15m['bb_dn']:
                    if curr_15m['stoch_k'] <= STOCH_PRO_LOW and prev_15m['stoch_k'] <= prev_15m['stoch_d'] and curr_15m['stoch_k'] > curr_15m['stoch_d']:
                        entry = curr_5m['close']
                        sl = entry - curr_15m['atr']
                        position = {'side': 'Buy', 'entry': entry, 'sl': sl, 'qty': (self.balance * POSITION_SIZE * LEVERAGE) / entry, 'tp1_hit': False, 'engine': 'SIDEWAYS', 'tp1': 0, 'tp2': 0}
                # Short Reclaim
                elif curr_5m['high'] > curr_15m['bb_up'] and curr_5m['close'] < curr_15m['bb_up']:
                    if curr_15m['stoch_k'] >= STOCH_PRO_HIGH and prev_15m['stoch_k'] >= prev_15m['stoch_d'] and curr_15m['stoch_k'] < curr_15m['stoch_d']:
                        entry = curr_5m['close']
                        sl = entry + curr_15m['atr']
                        position = {'side': 'Sell', 'entry': entry, 'sl': sl, 'qty': (self.balance * POSITION_SIZE * LEVERAGE) / entry, 'tp1_hit': False, 'engine': 'SIDEWAYS', 'tp1': 0, 'tp2': 0}

    def print_final_report(self):
        trades = np.array(self.trades)
        if len(trades) == 0: return print("No trades executed.")
        
        total_ret = (self.balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
        win_rate = (len(trades[trades > 0]) / len(trades)) * 100
        pf = abs(trades[trades > 0].sum() / trades[trades < 0].sum()) if len(trades[trades < 0]) > 0 else float('inf')
        
        equity_curve = np.cumsum(self.trades) + INITIAL_BALANCE
        peak = np.maximum.accumulate(equity_curve)
        mdd = np.max((peak - equity_curve) / peak) * 100 if len(equity_curve) > 0 else 0

        print("\n" + "═"*55)
        print(f"  V3.3 FULL HYBRID BACKTEST (6 MONTHS / 10 SYMBOLS)")
        print("═"*55)
        print(f"  {'Total Return:':<25} {total_ret:>15.2f}%")
        print(f"  {'Final Balance:':<25} ${self.balance:>14.2f}")
        print(f"  {'Total Trades:':<25} {len(trades):>15} trades")
        print(f"  {'Win Rate:':<25} {win_rate:>15.2f}%")
        print(f"  {'MDD:':<25} {mdd:>15.2f}%")
        print(f"  {'Profit Factor:':<25} {pf:>15.2f}")
        print("═"*55)

if __name__ == "__main__":
    tester = FullHybridBacktester()
    for s in SYMBOLS:
        try:
            tester.run_simulation(s)
        except Exception as e:
            log.error(f"Error on {s}: {e}")
    tester.print_final_report()
