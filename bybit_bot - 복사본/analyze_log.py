import pandas as pd
import numpy as np
def analyze_trade_log(file_path="backtest_trade_log.csv"):
    try:
        df = pd.read_csv(file_path)
        df['Entry_Time'] = pd.to_datetime(df['Entry_Time'])
        df['Exit_Time'] = pd.to_datetime(df['Exit_Time'])
        df['Win'] = df['PnL'] > 0
        df['Entry_Hour'] = df['Entry_Time'].dt.hour

        print("\n" + "="*60)
        print(f" QUANT TRADE LOG ANALYSIS (Total Trades: {len(df)})")
        print("="*60)

        print("\n[1] SYMBOL PERFORMANCE ANALYSIS")
        symbol_stats = df.groupby('Symbol').agg(
            Trades=('Symbol', 'count'),
            Win_Rate=('Win', lambda x: x.mean() * 100),
            Total_PnL=('PnL', 'sum')
        ).sort_values('Win_Rate', ascending=False)
        
        for idx, row in symbol_stats.iterrows():
            print(f" {idx:10s} | Trades: {row['Trades']:4.0f} | WinRate: {row['Win_Rate']:5.1f}% | PnL: {row['Total_PnL']:8.2f} USDT")

        print("\n[2] WORST ENTRY HOURS (UTC/KST)")
        hourly_stats = df.groupby('Entry_Hour').agg(
            Trades=('Entry_Hour', 'count'),
            Win_Rate=('Win', lambda x: x.mean() * 100),
            Total_PnL=('PnL', 'sum')
        ).sort_values('Total_PnL', ascending=True)
        
        for hour, row in hourly_stats.head(5).iterrows():
            kst = (hour + 9) % 24
            print(f" UTC {hour:02d} (KST {kst:02d}) | Trades: {row['Trades']:4.0f} | WinRate: {row['Win_Rate']:5.1f}% | PnL: {row['Total_PnL']:8.2f} USDT")

        print("\n[3] EXIT REASON ANALYSIS")
        reason_stats = df.groupby('Exit_Reason').agg(
            Count=('Exit_Reason', 'count'),
            Total_PnL=('PnL', 'sum')
        ).sort_values('Total_PnL', ascending=True)
        
        for idx, row in reason_stats.iterrows():
            print(f" {idx:15s} | Count: {row['Count']:4.0f} | Total PnL: {row['Total_PnL']:10.2f} USDT")

        print("\n" + "="*60)
        print(" ADVISORY: Filter symbols with WinRate < 35% and avoid worst hours.")
        print("="*60 + "\n")
    except Exception as e:
        print(f"Error during analysis: {e}")
    except FileNotFoundError:
        print("🚨 에러: 'backtest_trade_log.csv' 파일이 같은 폴더에 없습니다. 백테스트를 먼저 돌려주세요.")
if __name__ == "__main__":
    analyze_trade_log()
