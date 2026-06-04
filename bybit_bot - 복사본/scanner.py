import logging
import time
from market_data import get_session, api_call
from config import (
    VIP_SYMBOLS, BLACKLIST, SCAN_EXCLUDE_TOP, MIN_VOLUME,
    MIN_LISTING_DAYS, EXCLUDE_ASSETS, MAX_SPREAD_RATE
)

log = logging.getLogger(__name__)


async def filter_valid_symbols(session, symbol_list: list) -> list:
    """
    [V3.7] 동적 밴(Dynamic Blacklist) 필터링
    1. 상장 14일 미만 신생 코인 차단
    2. 특정 제외 자산(스테이블 등) 차단
    3. 스프레드 0.3% 초과 종목 차단
    """
    if not symbol_list: return []
    
    # [1] 상장 정보 및 스프레드 체크를 위한 데이터 일괄 조회
    # category="linear" 전체를 가져오는 것이 각 심볼별 호출보다 압도적으로 빠름
    instr_resp = await api_call(session.get_instruments_info, category="linear")
    ticker_resp = await api_call(session.get_tickers, category="linear")
    
    if not instr_resp or instr_resp.get("retCode") != 0: return symbol_list
    if not ticker_resp or ticker_resp.get("retCode") != 0: return symbol_list
    
    # 데이터 매핑 (빠른 조회를 위해 dict 변환)
    instr_map = {item["symbol"]: item for item in instr_resp["result"]["list"]}
    ticker_map = {item["symbol"]: item for item in ticker_resp["result"]["list"]}
    
    server_time_ms = int(time.time() * 1000)
    min_listing_ms = MIN_LISTING_DAYS * 24 * 60 * 60 * 1000
    
    valid_list = []
    for symbol in symbol_list:
        # [조건 1] 특정 문자열 포함 여부 (EXCLUDE_ASSETS)
        base_coin = symbol.replace("USDT", "")
        if any(asset in base_coin for asset in EXCLUDE_ASSETS):
            continue
            
        # [조건 2] 상장일 체크 (launchTime)
        instr = instr_map.get(symbol)
        if instr:
            launch_time = int(instr.get("launchTime", 0))
            if server_time_ms - launch_time < min_listing_ms:
                # log.info(f"  [Skip] {symbol}: 신규 상장 코인 (14일 미만)")
                continue
        
        # [조건 3] 스프레드 체크 (Spread Rate)
        tick = ticker_map.get(symbol)
        if tick:
            ask = float(tick.get("ask1Price", 0))
            bid = float(tick.get("bid1Price", 0))
            if bid > 0:
                spread_rate = (ask - bid) / bid
                if spread_rate > MAX_SPREAD_RATE:
                    # log.info(f"  [Skip] {symbol}: 스프레드 과다 ({spread_rate*100:.2f}%)")
                    continue
        
        valid_list.append(symbol)
        
    return valid_list


async def scan_top_symbols():
    """
    바이비트 실시간 거래대금 상위 종목 스캔 및 동적 필터링 적용.
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
            
            # [기본 필터] 블랙리스트 확인
            if symbol in BLACKLIST:
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

    # [2] 거래대금 50M 필터
    filtered_tickers = [t["symbol"] for t in all_usdt_tickers if t["turnover"] >= MIN_VOLUME]
    
    # [3] 동적 밴(Dynamic Blacklist) 필터 적용
    valid_watchlist = await filter_valid_symbols(session, filtered_tickers)
    
    # [4] 우선순위 종목(ETH, SOL) 최우선 배치
    priority_syms = ["ETHUSDT", "SOLUSDT"]
    final_watchlist = []
    
    # 우선순위 종목 중 필터를 통과한 것들 먼저 추가
    for p_sym in priority_syms:
        if p_sym in valid_watchlist:
            final_watchlist.append(p_sym)
            valid_watchlist.remove(p_sym)
            
    # 나머지 추가
    final_watchlist.extend(valid_watchlist)
    
    log.info(f"🔍 스캔 완료: {len(final_watchlist)}개 종목 감시 시작")
    return final_watchlist
