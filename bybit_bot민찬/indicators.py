import pandas as pd
import numpy as np
from config import ADX_LEN, EMA_LEN, ATR_LEN


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        return df

    if len(df) < 50:
        for col in ['adx', 'atr', 'ema200', 'vol_ma']:
            df[col] = np.nan
        return df

    h, l, c, v = df["high"], df["low"], df["close"], df["volume"]

    # ADX
    pdm = h.diff().clip(lower=0)
    mdm = (-l.diff()).clip(lower=0)
    pdm = pdm.where(pdm > mdm, 0.0)
    mdm = mdm.where(mdm > pdm, 0.0)
    tr  = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / ADX_LEN, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / ADX_LEN, adjust=False).mean() / atr
    mdi = 100 * mdm.ewm(alpha=1 / ADX_LEN, adjust=False).mean() / atr
    dx  = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).fillna(0)
    df["adx"]    = dx.ewm(alpha=1 / ADX_LEN, adjust=False).mean()

    # ATR
    df["atr"]    = atr

    # EMA
    df["ema200"] = c.ewm(span=EMA_LEN, adjust=False).mean()
    df["ema50"]  = c.ewm(span=50, adjust=False).mean()

    # 거래량 이평
    df["vol_ma"] = v.rolling(window=20).mean()

    # StochRSI (FVG 과열 필터용)
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    rsi_min = rsi.rolling(14).min()
    rsi_max = rsi.rolling(14).max()
    rng   = (rsi_max - rsi_min).replace(0, np.nan)
    df["stoch_k"] = ((rsi - rsi_min) / rng * 100).rolling(3).mean()

    return df
