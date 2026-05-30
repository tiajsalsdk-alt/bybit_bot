import backtrader as bt
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

# =================================================================
# [Quant Architect] Hybrid Portfolio Backtest - Fact-Based Refactoring
# =================================================================

class HybridQuantStrategy(bt.Strategy):
    params = (
        ('ema_period', 200),
        ('adx_period', 14),
        ('bb_period', 20),
        ('bb_dev', 2.0),
        ('atr_period', 14),
        ('time_stop_bars', 12),     
        ('leverage', 10),
        ('risk_pct_trend', 0.15),   
        ('risk_pct_sideway', 0.20), 
        ('half_tp_roe_trend', 0.10),
        ('half_tp_roe_sideway', 0.05),
    )

    def __init__(self):
        self.inds = {}
        for d in self.datas:
            self.inds[d] = {
                'ema200': bt.indicators.EMA(d.close, period=self.p.ema_period * 4),
                'adx': bt.indicators.ADX(d, period=self.p.adx_period * 4),
                'bb': bt.indicators.BollingerBands(d.close, period=self.p.bb_period, devfactor=self.p.bb_dev),
                'atr': bt.indicators.ATR(d, period=self.p.atr_period),
                'stoch': bt.indicators.Stochastic(d, period=14, period_dfast=3, period_dslow=3),
                'vol_sma': bt.indicators.SMA(d.volume, period=20),
                'd_low': bt.indicators.Lowest(d.low(-1), period=20),
                'd_high': bt.indicators.Highest(d.high(-1), period=20),
                'entry_bar': 0,
                'entry_price': 0.0,
                'half_tp_done': False,
                'engine_type': None,
                'stop_loss': 0.0,
                'take_profit': 0.0
            }
        self.total_trades = 0
        self.wins = 0

    def next(self):
        for d in self.datas:
            pos = self.getposition(d)
            ind = self.inds[d]
            
            if not pos:
                current_adx = ind['adx'][0]
                close = d.close[0]
                if 22 < current_adx < 25: continue

                # Logic 1: TREND (ADX >= 25)
                if current_adx >= 25:
                    if d.low[0] < ind['d_low'][0] and close > ind['d_low'][0] and d.volume[0] > ind['vol_sma'][0] * 1.2:
                        if close > ind['ema200'][0]:
                            self.execute_entry(d, 'Buy', close, ind['atr'][0], ind['bb'].mid[0], 'TREND')
                    elif d.high[0] > ind['d_high'][0] and close < ind['d_high'][0] and d.volume[0] > ind['vol_sma'][0] * 1.2:
                        if close < ind['ema200'][0]:
                            self.execute_entry(d, 'Sell', close, ind['atr'][0], ind['bb'].mid[0], 'TREND')

                # Logic 2: SIDEWAY (ADX <= 22)
                elif current_adx <= 22:
                    if close > ind['ema200'][0]: # Bullish Sideway
                        if d.low[-1] < ind['bb'].bot[-1] and close > ind['bb'].bot[0]:
                            if ind['stoch'].percK[0] > ind['stoch'].percD[0] and ind['stoch'].percK[0] <= 20:
                                self.execute_entry(d, 'Buy', close, ind['atr'][0], ind['bb'].mid[0], 'SIDEWAY')
                    elif close < ind['ema200'][0]: # Bearish Sideway
                        if d.high[-1] > ind['bb'].top[-1] and close < ind['bb'].top[0]:
                            if ind['stoch'].percK[0] < ind['stoch'].percD[0] and ind['stoch'].percK[0] >= 80:
                                self.execute_entry(d, 'Sell', close, ind['atr'][0], ind['bb'].mid[0], 'SIDEWAY')

            else:
                bars_since_entry = len(self) - ind['entry_bar']
                if bars_since_entry >= self.p.time_stop_bars:
                    self.close(data=d)
                    continue

                current_p = d.close[0]
                pnl_pct = (current_p - ind['entry_price']) / ind['entry_price'] if pos.size > 0 else (ind['entry_price'] - current_p) / ind['entry_price']
                roe = pnl_pct * self.p.leverage

                # [방어막] 반익절 및 본절 이동
                threshold = self.p.half_tp_roe_trend if ind['engine_type'] == 'TREND' else self.p.half_tp_roe_sideway
                if not ind['half_tp_done'] and roe >= threshold:
                    ind['half_tp_done'] = True
                    self.close(data=d, size=abs(pos.size) / 2)
                    ind['stop_loss'] = ind['entry_price'] 
                
                # 최종 탈출 체크
                if ind['engine_type'] == 'SIDEWAY':
                    if pos.size > 0:
                        if current_p >= ind['bb'].top[0]: self.close(data=d)
                        elif current_p <= ind['stop_loss']: self.close(data=d)
                    else:
                        if current_p <= ind['bb'].bot[0]: self.close(data=d)
                        elif current_p >= ind['stop_loss']: self.close(data=d)
                else: # TREND
                    atr = ind['atr'][0]
                    # TREND 최종 TP는 ATR 4배
                    tp_price = ind['entry_price'] + (atr * 4.0) if pos.size > 0 else ind['entry_price'] - (atr * 4.0)
                    if pos.size > 0:
                        if current_p >= tp_price: self.close(data=d)
                        elif current_p <= ind['stop_loss']: self.close(data=d)
                    else:
                        if current_p <= tp_price: self.close(data=d)
                        elif current_p >= ind['stop_loss']: self.close(data=d)

    def execute_entry(self, data, side, price, atr, bb_mid, engine_type):
        """[Refactored] Dynamic Sizing & Absolute TP/SL"""
        ind = self.inds[data]
        
        # 1. 포지션 사이즈 동적 계산
        risk_pct = self.p.risk_pct_sideway if engine_type == 'SIDEWAY' else self.p.risk_pct_trend
        equity = self.broker.getvalue()
        qty = (equity * risk_pct * self.p.leverage) / price
        
        # 2. TP/SL 절대 가격 강제 업데이트
        ind['engine_type'] = engine_type
        ind['entry_bar'] = len(self)
        ind['entry_price'] = price
        ind['half_tp_done'] = False
        
        # SL 설정: 2.0 ATR (노이즈 방어 강화)
        sl_dist = atr * 2.0
        if side == 'Buy':
            ind['stop_loss'] = price - sl_dist
            ind['take_profit'] = bb_mid # 1차 목표
        else:
            ind['stop_loss'] = price + sl_dist
            ind['take_profit'] = bb_mid # 1차 목표

        if side == 'Buy': self.buy(data=data, size=qty)
        else: self.sell(data=data, size=qty)

    def notify_trade(self, trade):
        if trade.isclosed:
            self.total_trades += 1
            if trade.pnlcomm > 0: self.wins += 1

def run_portfolio_backtest():
    cerebro = bt.Cerebro()
    cerebro.addstrategy(HybridQuantStrategy)
    
    data_path = r"C:\Users\juj76\backtest_data"
    blacklist = ["MATICUSDT", "USDCUSDT", "XAUTUSDT", "PAXGUSDT", "FDUSDUSDT", "USDTUSDT"]
    
    files = [f for f in os.listdir(data_path) if f.endswith("_15m.csv")]
    symbols_added = 0
    
    print("[*] Sourcing 180-Day Data (Fact-Based Refactoring)...")
    for f in files:
        symbol = f.replace("_15m.csv", "")
        if any(b in symbol for b in blacklist): continue
        datapath = os.path.join(data_path, f)
        if os.path.getsize(datapath) < 100000: continue 

        try:
            df = pd.read_csv(datapath)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            last_date = df['timestamp'].max()
            start_date = last_date - timedelta(days=180)
            df_filtered = df[df['timestamp'] >= start_date].copy()
            if len(df_filtered) < 1000: continue
            
            data = bt.feeds.PandasData(
                dataname=df_filtered.set_index('timestamp'),
                datetime=None, open='open', high='high', low='low', close='close', volume='volume',
                timeframe=bt.TimeFrame.Minutes, compression=15
            )
            cerebro.adddata(data, name=symbol)
            symbols_added += 1
            if symbols_added >= 15: break 
        except: continue

    cerebro.broker.setcash(300.0)
    cerebro.broker.setcommission(commission=0.00035) # 리베이트 반영 0.035%
    
    print(f'[*] 총 {symbols_added}개 종목 포트폴리오 구성 완료.')
    results = cerebro.run()
    strat = results[0]
    
    final_val = cerebro.broker.getvalue()
    win_rate = (strat.wins / strat.total_trades * 100) if strat.total_trades > 0 else 0
    
    print("\n" + "="*60)
    print(" [180-DAY HYBRID PORTFOLIO - REALISTIC VER.]")
    print("="*60)
    print(f" 최종 잔고:      {final_val:>12,.2f} USDT")
    print(f" 총 매매 횟수:   {strat.total_trades:>12} 회")
    print(f" 실질 승률:      {win_rate:>12.2f} %")
    print(f" 최종 수익률:    {(final_val - 300)/3:>12.2f} %")
    print("="*60 + "\n")

if __name__ == "__main__":
    run_portfolio_backtest()
