import os
import sys
import time
import asyncio
import logging
import datetime as dt
import ta

os.system("")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from market_data import fetch_candles, get_balance, get_session, api_call
from scanner import scan_top_symbols
from indicators import add_indicators
from regime import detect_regime, Regime
from strategies import get_signal
from order_manager import (
    calc_qty, calc_tp_sl, place_hybrid_order, monitor_positions,
    check_trade_approval, _entry_atr, get_open_positions
)
from config import (
    ADX_TF, SIDEWAYS_TF, TREND_TF, CANDLES_NEEDED,
    ADX_TREND_LEVEL, ADX_SIDEWAYS_LEVEL, MAIN_LEV, DAILY_LOSS_LIMIT
)

log = logging.getLogger(__name__)

watchlist: list[str] = []
symbol_adx = {} 
symbol_ema = {} 
current_max_positions = 0
_entry_regimes = {}

async def check_daily_loss(wallet: float) -> bool:
    """오늘 발생한 총 손실이 설정된 한도를 초과했는지 확인"""
    if wallet <= 0: return False
    session = get_session()
    now = dt.datetime.now(dt.timezone.utc)
    start_of_day = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    
    try:
        resp = await api_call(session.get_closed_pnl, category="linear", startTime=start_of_day)
        if not resp or resp.get("retCode") != 0: return False
        
        daily_pnl = sum(float(p["closedPnl"]) for p in resp["result"]["list"])
        loss_limit_amount = wallet * DAILY_LOSS_LIMIT
        
        if daily_pnl < -loss_limit_amount:
            log.critical(f"🛑 [RISK] 일일 손실 한도 초과! 매매를 정지합니다. (PnL: {daily_pnl:.2f}, Limit: -{loss_limit_amount:.2f})")
            return True
    except Exception as e:
        log.error(f"PnL 체크 에러: {e}")
    return False

def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    root.addHandler(console)

async def refresh_watchlist():
    global watchlist
    watchlist = await scan_top_symbols()

async def run_regime_update():
    global current_max_positions, symbol_adx, symbol_ema
    log.info(f"[{ADX_TF}m] 1H ADX 장세 및 대추세 필터(EMA 50) 업데이트")
    for symbol in watchlist:
        try:
            df_1h = add_indicators(await fetch_candles(symbol, ADX_TF))
            symbol_adx[symbol] = df_1h.iloc[-1]["adx"]
            symbol_ema[symbol] = df_1h.iloc[-1]["ema50"]
            if symbol == "BTCUSDT":
                adx_val = symbol_adx[symbol]
                # 데드존에서도 횡보 매매 허용을 위해 max_positions를 3으로 유지
                if adx_val >= ADX_TREND_LEVEL: current_max_positions = 5
                else: current_max_positions = 3
            log.info(f"  {symbol} (1H ADX: {symbol_adx[symbol]:.1f})")
            await asyncio.sleep(0.5)
        except: continue

async def run_entry_check(wallet: float):
    # [V3.5 글로벌 진입 방어] 거래소 실시간 포지션 연동 (중복 진입 원천 차단)
    positions = await get_open_positions()
    if current_max_positions == 0: return

    for symbol in watchlist:
        try:
            # [방어 1] 이미 포지션 보유 중이거나 활성 주문이 있으면 즉시 제외
            if any(p["symbol"] == symbol for p in positions): continue
            
            from order_manager import _active_limit_orders
            if symbol in _active_limit_orders: continue
            
            # [방어 2] 물리적 글로벌 락(cooldown.json) 확인
            from order_manager import is_physically_locked
            if is_physically_locked(symbol): continue
            
            adx_1h = symbol_adx.get(symbol, 0.0)
            ema_h1 = symbol_ema.get(symbol, 0.0)
            
            # 장세에 따른 타점 타임프레임 선택 (데드존 < 25 이므로 15m 할당)
            entry_tf = TREND_TF if adx_1h >= ADX_TREND_LEVEL else SIDEWAYS_TF
            
            df_1h = add_indicators(await fetch_candles(symbol, ADX_TF))
            df_entry = add_indicators(await fetch_candles(symbol, entry_tf))
            
            signal = get_signal(df_1h, df_entry, symbol, ema_h1)
            if signal:
                side, engine, entry_p, sl_p, ma20 = signal
                _entry_atr[symbol] = df_entry.iloc[-1]["atr"]
                
                if not check_trade_approval(side, entry_p, adx_1h, ema_h1, len(positions)): continue
                
                qty = await calc_qty(symbol, entry_p, engine)
                if qty > 0 and entry_p > 0:
                    # [V3.3] 구조적 손절가(sl_p)를 전달하여 R-Multiple 기반 익절가 동적 계산
                    tp, sl = calc_tp_sl(symbol, side, entry_p, _entry_atr[symbol], engine, sl_p)
                        
                    # 100% 지정가 매복 모드
                    await place_hybrid_order(symbol, side, qty, entry_p, tp, sl, engine, ma20)
                    _entry_regimes[symbol] = engine
        except Exception as e:
            log.error(f"{symbol} 진입 체크 에러: {e}")
            continue

async def main():
    setup_logging()
    log.info("🚀 V3.5 Master Full-Fill 하이브리드 엔진 가동 시작")
    await refresh_watchlist()
    await run_regime_update()

    # [V3.5] 시작 즉시 1회 체크 수행
    wallet = await get_balance()
    if wallet:
        if await check_daily_loss(wallet): return
        await run_entry_check(wallet)

    while True:
        try:
            wallet = await get_balance()
            if wallet is None:
                log.error("❌ 잔고 조회 실패! 통신 확인 필요. 30초 후 재시도.")
                await asyncio.sleep(30)
                continue

            if await check_daily_loss(wallet):
                log.warning("💤 일일 손실 한도 도달로 인해 오늘 매매를 정지합니다. (내일 다시 실행하세요)")
                await asyncio.sleep(3600)
                continue

            curr_pos = await get_open_positions()
            if curr_pos: await monitor_positions(curr_pos, _entry_regimes)

            now = dt.datetime.now()
            # 5분 정각 체크 (0, 5, 10...)
            if now.minute % 5 == 0 and now.second < 15:
                if now.minute % 15 == 0:
                    await refresh_watchlist()
                    await run_regime_update()
                await run_entry_check(wallet)
                await asyncio.sleep(60) # 중복 체크 방지
            else:
                # 봇 생존 확인용 하트비트 (30초마다)
                if now.second % 30 == 0:
                    next_run = 5 - (now.minute % 5)
                    log.info(f"  [Heartbeat] 봇 생동 중.. 다음 진입 체크까지 {next_run}분 내외")
                    await asyncio.sleep(1)

            await asyncio.sleep(1)
        except Exception as e:
            log.error(f"메인 루프 에러: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
