import time
import logging
import sys
import os
from market_data import get_session, fetch_candles, api_call, get_balance
from order_manager import get_instrument_info, round_to_tick, set_leverage
from indicators import add_indicators

# 로그 설정 (터미널 출력 극대화)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("FORCE-TEST")

def run_api_integrity_test():
    symbol = "BTCUSDT"
    side = "Buy" # LONG 강제 진입
    
    log.info(f"🚀 [FORCE TEST] {symbol} API 주문 실행 모듈 강제 점검 시작")
    
    try:
        # 1. 자산 및 권한 팩트체크
        wallet = get_balance()
        info = get_instrument_info(symbol)
        log.info(f"[*] 현 잔고: {wallet:.2f} USDT | {symbol} 최소 주문 수량: {info['min_qty']}")

        # 2. 레버리지 서버 동기화 (10배)
        log.info("[*] 레버리지 10배 설정 시도...")
        if not set_leverage(symbol, 10):
            log.error("❌ 레버리지 설정 실패! (API 키 권한 'Contract-Trade' 확인 필요)")
            return

        # 3. 실시간 ATR 기반 SL/TP 계산 (명세: SL 1.5배)
        df = fetch_candles(symbol, "15", limit=50)
        df = add_indicators(df)
        atr_val = df.iloc[-1]["atr"]
        curr_price = df.iloc[-1]["close"]
        
        # SL: 1.5 ATR / TP: ROE 5% (10배 레버리지 시 가격 0.5% 변동)
        sl_price = round_to_tick(curr_price - (atr_val * 1.5), info["tick_size"])
        tp_price = round_to_tick(curr_price * 1.005, info["tick_size"]) 
        
        log.info(f"[*] 테스트 설계: 진입 {curr_price} | SL {sl_price} | TP(ROE 5%) {tp_price}")

        # 4. 강제 시장가 주문 발송 (SL/TP 동시 접수)
        log.info(f"[*] {symbol} {info['min_qty']}개 시장가 LONG 발송 중...")
        session = get_session()
        params = dict(
            category="linear", symbol=symbol, side=side, orderType="Market", 
            qty=str(info["min_qty"]), positionIdx=0,
            takeProfit=str(tp_price), stopLoss=str(sl_price),
            tpTriggerBy="LastPrice", slTriggerBy="LastPrice",
            tpslMode="Full", timeInForce="GTC"
        )
        
        resp = api_call(session.place_order, **params)
        
        if resp and resp.get("retCode") == 0:
            log.info("✅ [SUCCESS] API 주문 및 청산 방어막(SL/TP) 서버 접수 완료!")
            log.info(f"[*] 주문 ID: {resp['result']['orderId']}")
            log.info("[!] 바이비트 앱/웹에서 포지션과 SL/TP가 정상 노출되는지 확인하십시오.")
        else:
            msg = resp.get("retMsg") if resp else "No Response"
            err_code = resp.get("retCode") if resp else "Unknown"
            log.error(f"❌ [FAILED] 주문 거절! 코드: {err_code} | 메시지: {msg}")
            
            # 원인 분석 리포트
            if err_code == 110007: log.error("👉 분석: 잔고 부족 (Available Balance 확인 요망)")
            elif err_code == 10003: log.error("👉 분석: API 키 권한 부족 (Read-Write 확인 요망)")
            elif err_code == 10001: log.error("👉 분석: 파라미터 오류 (수량 단위 확인 요망)")
            
    except Exception as e:
        log.error(f"🔥 [CRITICAL] 시스템 예외 발생: {e}")
        log.error("👉 분석: 네트워크 단절 또는 라이브러리 충돌 가능성")

if __name__ == "__main__":
    run_api_integrity_test()
