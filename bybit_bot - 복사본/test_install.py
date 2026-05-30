import sys
sys.stdout = open("test_result.txt", "w", encoding="utf-8")
sys.stderr = sys.stdout

try:
    import ta
    print("ta: OK")
    import pybit
    print("pybit: OK")
    import schedule
    print("schedule: OK")
    import requests
    print("requests: OK")
    import pandas as pd
    import numpy as np
    print("pandas/numpy: OK")

    from indicators import add_indicators
    df = pd.DataFrame({
        "ts":     pd.date_range("2024-01-01", periods=300, freq="15min"),
        "open":   np.random.uniform(40000, 50000, 300),
        "high":   np.random.uniform(40000, 50000, 300),
        "low":    np.random.uniform(40000, 50000, 300),
        "close":  np.random.uniform(40000, 50000, 300),
        "volume": np.random.uniform(100, 1000, 300),
    })
    result = add_indicators(df)
    print(f"indicators: OK — 컬럼: {list(result.columns)}")
    print("모든 테스트 통과!")

except Exception as e:
    print(f"ERROR: {e}")

sys.stdout.close()
