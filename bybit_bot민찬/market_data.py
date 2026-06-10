import time
import logging
import pandas as pd
import os
from pybit.unified_trading import HTTP
from config import API_KEY, API_SECRET, DEMO

log = logging.getLogger(__name__)

# ANSI Color Codes
os.system('') # Enable ANSI colors on Windows
COLOR_RED = '\033[91m'
COLOR_RESET = '\033[0m'

_session: HTTP | None = None


def get_session() -> HTTP:
    global _session
    if _session is None:
        _session = HTTP(
            demo=DEMO,
            api_key=API_KEY,
            api_secret=API_SECRET,
            recv_window=60000,
        )
    return _session


import asyncio

async def api_call(func, *args, **kwargs):
    """
    [명세서 3] 스마트 백오프(Smart Backoff) 도입
    10006 에러 발생 시 리셋 시간을 계산하여 대기 후 재시도.
    """
    max_retries = 5
    for attempt in range(max_retries):
        try:
            # pybit는 동기 라이브러리이므로 별도 스레드에서 실행하여 비동기 루프 보존
            resp = await asyncio.to_thread(func, *args, **kwargs)
            
            # [Bug 7/110043] 이미 설정된 레버리지는 무시
            ret_code = resp.get("retCode")
            if ret_code == 0:
                return resp
            
            if ret_code == 10006:
                # [명세서 3] 스마트 백오프: 10006 에러 시 안전하게 2초 이상 대기 (또는 리셋 타임 파싱)
                log.warning(f"Rate Limit Hit (10006). {attempt+1}/{max_retries} 스마트 대기 중...")
                await asyncio.sleep(2 ** attempt + 1)
                continue
            
            # [명세서 3] 10001 등 논리적 파라미터 에러는 재시도 없이 즉시 중단
            if ret_code == 10001:
                log.error(f"Logic Error (10001): {resp.get('retMsg')}. 재시도 중단.")
                return resp
                
            return resp
            
        except Exception as e:
            err_msg = str(e)
            if "110043" in err_msg:
                return {"retCode": 0, "retMsg": "Already set"}
            
            # [명세서 3] 10001 등 논리적 에러가 예외로 터질 경우 즉시 중단
            if "10001" in err_msg:
                log.error(f"{COLOR_RED}[Error Trace] Logic Error Exception (10001): {err_msg}. 재시도 중단.{COLOR_RESET}")
                return {"retCode": 10001, "retMsg": err_msg}

            # [명세서 3] 10006 에러가 예외로 터질 경우 대응
            if "10006" in err_msg:
                wait_time = 2 ** attempt + 1
                log.warning(f"{COLOR_RED}[Error Trace] Rate Limit (10006) Exception. {wait_time}초 후 재시도...{COLOR_RESET}")
                await asyncio.sleep(wait_time)
                continue

            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                log.error(f"{COLOR_RED}[Error Trace] API Error ({err_msg}). {attempt+1}/{max_retries} 재시도 중... ({wait_time}초 대기){COLOR_RESET}")
                await asyncio.sleep(wait_time)
            else:
                log.critical(f"{COLOR_RED}[Error Trace] API Failure after {max_retries} attempts: {e}{COLOR_RESET}")
                raise RuntimeError("CIRCUIT_BREAKER_TRIGGERED")
    return None


async def fetch_candles(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
    session = get_session()
    resp = await api_call(session.get_kline,
        category="linear",
        symbol=symbol,
        interval=interval,
        limit=limit,
    )
    if not resp or resp.get("retCode") != 0:
        msg = resp.get("retMsg") if resp else "No Response"
        raise RuntimeError(f"[{symbol}] kline error: {msg}")

    rows = resp["result"]["list"]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df = df.astype({
        "ts": "int64", "open": "float64", "high": "float64",
        "low": "float64", "close": "float64", "volume": "float64",
    })
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.sort_values("ts").reset_index(drop=True)
    return df


async def get_funding_rates() -> dict:
    """USDT 선물 전종목 현재 펀딩비 반환 {symbol: rate}"""
    try:
        session = get_session()
        resp = await api_call(session.get_tickers, category="linear")
        if resp and resp.get("retCode") == 0:
            result = {}
            for t in resp["result"]["list"]:
                sym = t.get("symbol", "")
                if sym.endswith("USDT"):
                    try:
                        result[sym] = float(t.get("fundingRate", 0))
                    except (ValueError, TypeError):
                        pass
            return result
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
        log.error(f"펀딩비 조회 오류: {e}")
    return {}


async def get_balance() -> float:
    session = get_session()
    resp = await api_call(session.get_wallet_balance, accountType="UNIFIED", coin="USDT")
    if not resp or resp.get("retCode") != 0:
        msg = resp.get("retMsg") if resp else "No Response"
        raise RuntimeError(f"Balance error: {msg}")
    
    for coin in resp["result"]["list"][0]["coin"]:
        if coin["coin"] == "USDT":
            val = (coin.get("availableToWithdraw") or
                   coin.get("availableBalance") or
                   coin.get("walletBalance") or "0")
            return float(val)
    return 0.0
