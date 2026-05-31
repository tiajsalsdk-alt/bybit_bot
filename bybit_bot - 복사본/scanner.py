import logging
from market_data import get_session, api_call
from config import VIP_SYMBOLS, BLACKLIST, SCAN_EXCLUDE_TOP, MIN_VOLUME

log = logging.getLogger(__name__)

_EXCLUDE_BASES = {"USDC", "DAI", "BUSD", "USDP", "TUSD", "FDUSD", "USDE", "PYUSD", "XAUT", "PAXG", "EUR", "GBP"}


async def scan_top_symbols():
    """
    바이비트 실시간 거래대금 상위 종목 스캔 (금/스테이블/법정화폐 페어 제외).
    """
    try:
        session = get_session()
        resp = await api_call(session.get_tickers, category="linear")
        if not resp or resp.get("retCode") != 0:
            msg = resp.get("retMsg") if resp else "No Response"
            log.error(f"티커 조회 에러: {msg}")
            return VIP_SYMBOLS

        tickers = resp["result"]["list"]
        all_usdt_tickers = []

        for t in tickers:
            symbol = t["symbol"]
            # USDT 페어만 포함
            if not symbol.endswith("USDT"):
                continue
            
            # [필터 1] 스테이블 및 금 페어 원천 차단
            base_coin = symbol.replace("USDT", "")
            if base_coin in _EXCLUDE_BASES:
                continue

            try:
                turnover = float(t["turnover24h"])
                all_usdt_tickers.append({
                    "symbol": symbol,
                    "turnover": turnover
                })
            except:
                continue

    except Exception as e:
        log.error(f"스캔 중 예외 발생: {e}")
        return VIP_SYMBOLS

    # 거래대금 내림차순 정렬
    all_usdt_tickers.sort(key=lambda x: x["turnover"], reverse=True)

    # [2] 거래대금 50M 필터 및 ETH, SOL 최우선 배치
    priority_syms = ["ETHUSDT", "SOLUSDT"]
    filtered_tickers = [t for t in all_usdt_tickers if t["turnover"] >= MIN_VOLUME]
    
    # 우선순위 종목을 리스트 최상단으로, 나머지는 거래대금 순
    filtered_tickers.sort(key=lambda x: (x["symbol"] in priority_syms, x["turnover"]), reverse=True)
    
    watchlist = [t["symbol"] for t in filtered_tickers if t["symbol"] not in BLACKLIST]
    return watchlist
