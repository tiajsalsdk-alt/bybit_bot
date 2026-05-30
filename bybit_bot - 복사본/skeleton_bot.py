import asyncio
import time
import ccxt
import ccxt.pro as ccxtpro
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# 거래소 설정 (기본 바이비트 선형 선물)
exchange_id = 'bybit'
exchange_config = {
    'options': {'defaultType': 'linear'},  # 선물(Linear) 모드
    'enableRateLimit': True,
}

def get_safe_top_10_coins():
    """
    1단계: 우량 코인 10개 추출
    - USDT 페어, 레버리지 토큰 제외, 거래대금 1000만 달러 이상, 200시간 이상 데이터 보유 확인
    """
    exchange = getattr(ccxt, exchange_id)(exchange_config)
    log.info(f"{exchange_id}에서 우량 종목 스캔 시작...")
    
    try:
        # 1. 모든 티커 호출
        tickers = exchange.fetch_tickers()
        
        # 2. 1차 필터링: USDT 페어 + 레버리지 토큰 제외 + 거래대금 1000만$ 이상
        candidate_list = []
        for symbol, ticker in tickers.items():
            # USDT 페어만 선택 (선물 기준 'BTC/USDT:USDT' 또는 'BTCUSDT')
            if not ('/USDT' in symbol or symbol.endswith('USDT')):
                continue
            
            # 레버리지 토큰 제외
            base = symbol.split('/')[0].split(':')[0].upper()
            if any(word in base for word in ['UP', 'DOWN', 'BULL', 'BEAR']):
                continue
            
            # 24시간 거래대금 (quoteVolume) 확인
            quote_volume = ticker.get('quoteVolume', 0)
            if quote_volume < 10_000_000:
                continue
            
            candidate_list.append({
                'symbol': symbol,
                'volume': quote_volume
            })
        
        # 3. 거래대금 순 내림차순 정렬
        candidate_list.sort(key=lambda x: x['volume'], reverse=True)
        
        # 4. 상위 코인부터 200개 캔들 존재 여부 검증 (최대 10개 추출)
        final_10_coins = []
        for item in candidate_list:
            symbol = item['symbol']
            try:
                # 1시간봉 기준 200개 데이터 조회
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=200)
                
                if len(ohlcv) >= 200:
                    final_10_coins.append(symbol)
                    log.info(f"[{len(final_10_coins)}/10] 합격: {symbol} (거래대금: ${item['volume']:,.0f})")
                else:
                    log.warning(f"데이터 부족 탈락: {symbol} ({len(ohlcv)}개)")
                
                # Rate Limit 방지
                time.sleep(0.1)
                
                # 10개가 채워지면 즉시 종료
                if len(final_10_coins) >= 10:
                    break
                    
            except Exception as e:
                log.error(f"{symbol} 조회 중 오류: {e}")
                continue
        
        log.info(f"최종 선정된 코인 ({len(final_10_coins)}개): {final_10_coins}")
        return final_10_coins

    except Exception as e:
        log.error(f"스캐너 작동 중 심각한 오류 발생: {e}")
        return []

async def start_websocket_monitor(coins):
    """
    2단계: 통합 웹소켓 모니터링
    - 멀티플렉싱을 통해 단일 연결로 여러 코인 감지 + 자동 재연결 로직
    """
    if not coins:
        log.error("모니터링할 코인 리스트가 없습니다.")
        return

    # CCXT Pro 거래소 객체 생성
    exchange_ws = getattr(ccxtpro, exchange_id)(exchange_config)
    
    log.info(f"{len(coins)}개 종목 실시간 모니터링 시작 (Websocket)...")
    
    while True:
        try:
            # watchTickers를 통해 여러 심볼을 단일 소켓 스트림으로 수신 (멀티플렉싱)
            # 수신된 티커 데이터는 딕셔너리 형태 {symbol: ticker_data}
            tickers = await exchange_ws.watch_tickers(coins)
            
            for symbol in coins:
                if symbol in tickers:
                    ticker = tickers[symbol]
                    last_price = ticker.get('last')
                    change = ticker.get('percentage')
                    # 여기서 매매 전략 판단 로직 호출 가능
                    print(f"\r[실시간] {symbol}: {last_price:,.4f} ({change:+.2f}%)", end="")
            
        except Exception as e:
            log.warning(f"웹소켓 연결 끊김 또는 오류 발생: {e}")
            log.info("3초 후 재연결을 시도합니다...")
            await asyncio.sleep(3)
            # 기존 연결 초기화 후 재시도
            try:
                await exchange_ws.close()
            except:
                pass
            exchange_ws = getattr(ccxtpro, exchange_id)(exchange_config)

if __name__ == "__main__":
    # 1. 우량 종목 10개 추출 (REST API)
    top_10_coins = get_safe_top_10_coins()
    
    if top_10_coins:
        # 2. 실시간 모니터링 실행 (WebSocket)
        try:
            asyncio.run(start_websocket_monitor(top_10_coins))
        except KeyboardInterrupt:
            log.info("사용자에 의해 프로그램이 종료되었습니다.")
