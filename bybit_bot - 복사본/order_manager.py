import math
import time
import asyncio
import logging
import json
import os
from market_data import get_session, fetch_candles, api_call, get_balance
from notifier import send
from indicators import add_indicators
from config import (
    MAIN_LEV, 
    WEIGHT_SIDEWAYS, WEIGHT_TREND,
    LIMIT_TIMEOUT, ATR_SL_MULT_TREND, ATR_TP_MULT_TREND, ATR_SL_MULT_SIDEWAYS,
    ROE_TAKE_PROFIT_PCT, TREND_HALF_TP_ROE, BB_LEN, BB_STD,
    ADX_TREND_LEVEL, ADX_SIDEWAYS_LEVEL, FEE_BUFFER, ENTRY_TIMEOUT,
    SIDEWAYS_TF
)

log = logging.getLogger(__name__)

# --- [상태 관리 및 영속화] ---
COOLDOWN_FILE = "cooldowns.json"
_half_tp_done = set()
_entry_atr = {}      
_instrument_info_cache = {}
_entry_times = {}    

def save_cooldown_data(losses, until):
    try:
        with open(COOLDOWN_FILE, "w") as f:
            json.dump({"consecutive_losses": losses, "cooldown_until": until}, f)
    except Exception as e:
        log.error(f"쿨다운 데이터 저장 실패: {e}")

def load_cooldown_data():
    try:
        if os.path.exists(COOLDOWN_FILE):
            with open(COOLDOWN_FILE, "r") as f:
                data = json.load(f)
                return data.get("consecutive_losses", 0), data.get("cooldown_until", 0.0)
    except Exception as e:
        log.error(f"쿨다운 데이터 로드 실패: {e}")
    return 0, 0.0

# 초기 로드
consecutive_losses, cooldown_until = load_cooldown_data()

async def get_instrument_info(symbol: str):
    if symbol in _instrument_info_cache:
        return _instrument_info_cache[symbol]
    try:
        session = get_session()
        resp = await api_call(session.get_instruments_info, category="linear", symbol=symbol)
        if resp and resp.get("retCode") == 0:
            info = resp["result"]["list"][0]
            data = {
                "tick_size": float(info["priceFilter"]["tickSize"]),
                "qty_step": float(info["lotSizeFilter"]["qtyStep"]),
                "min_qty": float(info["lotSizeFilter"]["minOrderQty"])
            }
            _instrument_info_cache[symbol] = data
            return data
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
        log.error(f"[{symbol}] 정보 획득 실패: {e}")
    return {"tick_size": 0.0001, "qty_step": 0.01, "min_qty": 0.01}

def round_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0: return price
    return round(round(price / tick_size) * tick_size, 8)

async def set_leverage(symbol: str, leverage: int) -> bool:
    try:
        session = get_session()
        resp = await api_call(session.set_leverage, category="linear", symbol=symbol,
                        buyLeverage=str(leverage), sellLeverage=str(leverage))
        if resp and resp.get("retCode") in [0, 110043]:
            return True
        log.error(f"[{symbol}] 레버리지 설정 실패: {resp.get('retMsg')}")
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
        log.error(f"[{symbol}] 레버리지 설정 중 예외: {e}")
    return False

async def update_sl(symbol: str, new_sl: float):
    max_retries = 3
    for i in range(max_retries):
        try:
            session = get_session()
            resp = await api_call(session.set_trading_stop, category="linear", symbol=symbol,
                     stopLoss=str(new_sl), slTriggerBy="LastPrice", positionIdx=0)
            if resp and resp.get("retCode") == 0:
                return True
            log.error(f"[{symbol}] SL 업데이트 실패 ({i+1}/{max_retries}): {resp.get('retMsg')}")
        except Exception as e:
            if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
            log.error(f"[{symbol}] SL 업데이트 중 예외 발생 ({i+1}/{max_retries}): {e}")
        await asyncio.sleep(1)
    return False

def handle_trade_result(is_win: bool):
    global consecutive_losses, cooldown_until
    if is_win:
        consecutive_losses = 0
        log.info("🎯 익절 성공! 연속 손절 카운트 초기화.")
    else:
        consecutive_losses += 1
        log.warning(f"❌ 손절 발생 ({consecutive_losses}/3)")
        if consecutive_losses >= 3:
            cooldown_until = time.time() + 3600
            log.critical("🚨 [COOL DOWN] 3연속 손절 발생! 1시간 동안 신규 진입을 중단합니다.")
    save_cooldown_data(consecutive_losses, cooldown_until)

def check_cooldown():
    l, u = load_cooldown_data()
    if time.time() < u:
        return True
    return False

async def calc_qty(symbol: str, entry_price: float, current_wallet: float, engine_name: str) -> float:
    try:
        wallet_usdt = await get_balance()
    except Exception as e:
        log.error(f"[{symbol}] 잔고 조회 실패: {e}")
        wallet_usdt = current_wallet

    weight = WEIGHT_TREND if "TREND" in engine_name else WEIGHT_SIDEWAYS
    margin = wallet_usdt * weight
    
    if margin <= 0:
        log.warning(f"[{symbol}] 진입 마진 부족 (Weight: {weight}, Wallet: {wallet_usdt:.2f})")
        return 0.0

    notional = margin * MAIN_LEV
    raw_qty = notional / entry_price
    
    info = await get_instrument_info(symbol)
    qty = math.floor(raw_qty / info["qty_step"]) * info["qty_step"]
    return round(qty, 8)

def calc_tp_sl(symbol: str, side: str, entry_p: float, atr: float, engine_name: str):
    if "SIDEWAYS" in engine_name:
        if side == "Buy":
            sl = entry_p - (atr * ATR_SL_MULT_SIDEWAYS)
            tp = entry_p + (atr * 2.0) 
        else:
            sl = entry_p + (atr * ATR_SL_MULT_SIDEWAYS)
            tp = entry_p - (atr * 2.0)
    else:
        sl = entry_p - (atr * ATR_SL_MULT_TREND) if side == "Buy" else entry_p + (atr * ATR_SL_MULT_TREND)
        tp = entry_p + (atr * ATR_TP_MULT_TREND) if side == "Buy" else entry_p - (atr * ATR_TP_MULT_TREND)
    _entry_atr[symbol] = atr
    return tp, sl

async def is_order_filled(symbol: str, order_id: str) -> bool:
    try:
        session = get_session()
        resp = await api_call(session.get_order_history, category="linear", symbol=symbol, orderId=order_id)
        if resp and resp.get("retCode") == 0:
            order_list = resp["result"]["list"]
            if order_list and order_list[0]["orderStatus"] == "Filled":
                return True
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
    return False

# [유틸리티] 소수점 정밀도 및 규격 포맷팅
def format_precision(value: float, step: float, is_floor: bool = False) -> str:
    if not value or step <= 0: return "0"
    p = len(str(float(step)).rstrip('0').split('.')[-1]) if '.' in str(float(step)) else 0
    if is_floor:
        multi = 10 ** p
        val = math.floor(round(value * multi, 10)) / multi
    else:
        val = round(round(value / step) * step, p)
    return f"{val:.{p}f}"

async def place_hybrid_order(symbol: str, side: str, qty: float, entry_price: float, tp_price: float, sl_price: float, is_entry: bool = True, engine_name: str = "", ma20_target: float = 0.0) -> bool:
    if not await set_leverage(symbol, MAIN_LEV): return False
    session = get_session(); info = await get_instrument_info(symbol)
    tick = info["tick_size"]; step = info["qty_step"]
    
    qty_s = format_precision(qty, step, is_floor=True)
    entry_s = format_precision(entry_price, tick)
    
    common = dict(category="linear", symbol=symbol, side=side, qty=qty_s, positionIdx=0, timeInForce="GTC")

    try:
        # 1. 지정가(Limit) 매복 시도 (최초 TP/SL 포함)
        resp = await api_call(session.place_order, **common, orderType="Limit", price=entry_s,
                             takeProfit=format_precision(tp_price, tick),
                             stopLoss=format_precision(sl_price, tick),
                             tpTriggerBy="LastPrice", slTriggerBy="LastPrice")
        
        if resp and resp.get("retCode") == 0:
            order_id = resp["result"]["orderId"]
            log.info(f"🎯 [{symbol}] 지정가 매복 (@{entry_s}). {ENTRY_TIMEOUT}초 대기...")
            await asyncio.sleep(ENTRY_TIMEOUT)
            
            # 2. 체결 확인 및 팰백 (Timeout Fallback)
            if await is_order_filled(symbol, order_id):
                log.info(f"✅ {side} {symbol} 지정가 체결 완료!")
                act_entry = entry_price 
            else:
                await api_call(session.cancel_order, category="linear", symbol=symbol, orderId=order_id)
                print(f"  [Timeout] {symbol} - 지정가 미체결로 5초 후 시장가 전환")
                
                # ① 순수 시장가 진입 주문 (에러 방지용 TP/SL 제외)
                resp2 = await api_call(session.place_order, category="linear", symbol=symbol, side=side, 
                                     orderType="Market", qty=qty_s, positionIdx=0)
                
                if resp2 and resp2.get("retCode") == 0:
                    await asyncio.sleep(1.0) # 포지션 갱신 대기
                    pos_resp = await api_call(session.get_positions, category="linear", symbol=symbol)
                    if pos_resp and pos_resp.get("retCode") == 0:
                        pos_info = pos_resp["result"]["list"][0]
                        act_entry = float(pos_info["avgPrice"])
                        
                        # ② 실제 체결가 기준 TP/SL 재계산
                        atr = _entry_atr.get(symbol, 0.0)
                        if side == "Buy":
                            new_tp = act_entry + (atr * ATR_TP_MULT_TREND)
                            new_sl = act_entry - (atr * ATR_SL_MULT_TREND)
                        else:
                            new_tp = act_entry - (atr * ATR_TP_MULT_TREND)
                            new_sl = act_entry + (atr * ATR_SL_MULT_TREND)
                            
                        await api_call(session.set_trading_stop, category="linear", symbol=symbol, positionIdx=0,
                                     takeProfit=format_precision(new_tp, tick),
                                     stopLoss=format_precision(new_sl, tick),
                                     tpTriggerBy="LastPrice", slTriggerBy="LastPrice")
                        log.info(f"⚡ {symbol} 시장가 체결 및 TP/SL 재설정 완료 (@{act_entry})")
                    else: return False
                else: return False

            # 3. 반익절 예약 (지정가 Maker)
            if is_entry:
                _entry_times[symbol] = time.time()
                half_qty = format_precision(qty * 0.5, step, is_floor=True)
                final_tp = ma20_target if "SIDEWAYS" in engine_name else (act_entry + (_entry_atr.get(symbol, 0) * 1.8) if side=="Buy" else act_entry - (_entry_atr.get(symbol, 0) * 1.8))
                
                await api_call(session.place_order, category="linear", symbol=symbol, 
                         side="Sell" if side == "Buy" else "Buy", orderType="Limit", 
                         price=format_precision(final_tp, tick), qty=half_qty, 
                         reduceOnly=True, postOnly=True, positionIdx=0)
                log.info(f"💰 [{symbol}] 50% 반익절 예약 완료 @{final_tp}")
            return True
    except Exception as e:
        log.error(f"⚠️ 주문 예외: {e}")
    return False

async def monitor_positions(positions: list, entry_regimes: dict):
    if not positions:
        _half_tp_done.clear()
        return
    active_symbols = {p["symbol"] for p in positions}
    for sym in list(_half_tp_done):
        if sym not in active_symbols: _half_tp_done.discard(sym)

    for pos in positions:
        sym = pos["symbol"]; side = pos["side"]; qty = float(pos["size"])
        entry_p = float(pos["avgPrice"]); current_p = float(pos["markPrice"])
        lev = float(pos.get("leverage", 10))
        engine = entry_regimes.get(sym, "")

        # --- [FVG 전용] 본절 이동 (Break-even) 로직 ---
        if engine == "TREND_FVG" and sym in _half_tp_done:
            fee_buffer_price = entry_p * (1 + FEE_BUFFER) if side == "Buy" else entry_p * (1 - FEE_BUFFER)
            await update_sl(sym, fee_buffer_price)

        entry_ts = _entry_times.get(sym)
        if entry_ts and (time.time() - entry_ts > 3600):
            log.warning(f"⏰ [{sym}] 타임 스탑 청산 (12캔들 경과)")
            await close_position_market(sym, side, qty)
            _entry_times.pop(sym, None); continue

        roe = ((current_p - entry_p) / entry_p if side == "Buy" else (entry_p - current_p) / entry_p) * lev

        if "SIDEWAYS" in engine:
            try:
                df = await fetch_candles(sym, SIDEWAYS_TF, limit=25)
                ma = df['close'].rolling(window=BB_LEN).mean().iloc[-1]
                bb_up = ma + (df['close'].rolling(window=BB_LEN).std().iloc[-1] * BB_STD)
                bb_dn = ma - (df['close'].rolling(window=BB_LEN).std().iloc[-1] * BB_STD)
                
                hit_mid = (current_p >= ma) if side == "Buy" else (current_p <= ma)
                if sym not in _half_tp_done and hit_mid:
                    info = await get_instrument_info(sym)
                    half_qty = format_precision(qty * 0.5, info["qty_step"], True)
                    if float(half_qty) >= info["min_qty"]:
                        log.critical(f"🚀 [{sym}] 횡보로직: 볼벤 중앙선 반익절 (시장가)!")
                        close_side = "Sell" if side == "Buy" else "Buy"
                        await api_call(get_session().place_order, category="linear", symbol=sym, side=close_side,
                                 orderType="Market", qty=str(half_qty), reduceOnly=True, positionIdx=0)
                        await update_sl(sym, entry_p); _half_tp_done.add(sym)
                        continue 

                hit_target = (current_p >= bb_up) if side == "Buy" else (current_p <= bb_dn)
                if hit_target:
                    log.critical(f"💰 [{sym}] 횡보로직: 목표가 도달 전량 익절!")
                    await close_position_market(sym, side, qty); continue
            except Exception as e:
                log.error(f"모니터링 오류: {e}")
            continue

        if sym not in _half_tp_done and roe >= TREND_HALF_TP_ROE:
            info = await get_instrument_info(sym)
            half_qty = format_precision(qty * 0.5, info["qty_step"], True)
            if float(half_qty) >= info["min_qty"]:
                log.critical(f"🚀 [{sym}] {engine} ROE {TREND_HALF_TP_ROE*100:.0f}% 반익절 (지정가)!")
                close_side = "Sell" if side == "Buy" else "Buy"
                tp_p = current_p
                await api_call(get_session().place_order, category="linear", symbol=sym, side=close_side,
                         orderType="Limit", price=format_precision(tp_p, info["tick_size"]), 
                         qty=half_qty, reduceOnly=True, postOnly=True, positionIdx=0)
                await update_sl(sym, entry_p); _half_tp_done.add(sym)

async def close_all_active_positions():
    try:
        session = get_session()
        resp = await api_call(session.get_positions, category="linear", settleCoin="USDT")
        if resp and resp.get("retCode") == 0:
            for pos in resp["result"]["list"]:
                if float(pos["size"]) > 0:
                    await close_position_market(pos["symbol"], pos["side"], float(pos["size"]))
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e

async def cancel_all_active_orders(symbol: str):
    try:
        session = get_session()
        open_orders = await api_call(session.get_open_orders, category="linear", symbol=symbol)
        if open_orders and open_orders.get("retCode") == 0 and open_orders["result"]["list"]:
            await api_call(session.cancel_all_orders, category="linear", symbol=symbol)
            return True
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
    return False

async def close_position_market(symbol: str, side: str, qty: float):
    close_side = "Sell" if side == "Buy" else "Buy"
    max_retries = 3
    for i in range(max_retries):
        try:
            resp = await api_call(get_session().place_order, category="linear", symbol=symbol, side=close_side,
                     orderType="Market", qty=str(qty), reduceOnly=True, positionIdx=0, timeInForce="IOC")
            if resp and resp.get("retCode") == 0:
                log.info(f"✅ [{symbol}] 시장가 청산 성공.")
                await cancel_all_active_orders(symbol)
                return True
        except Exception as e:
            if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
            log.error(f"⚠️ [{symbol}] 청산 중 예외 발생 ({i+1}/{max_retries}): {e}")
        await asyncio.sleep(2)
    return False

def check_trade_approval(signal_type, target_price, adx_htf, ema_htf, current_position_count):
    if check_cooldown():
        log.warning("❄️ [COOL DOWN] 쿨다운 중.")
        return False
    
    if adx_htf < ADX_TREND_LEVEL: # 횡보장 (최대 3개)
        return current_position_count < 3
        
    if adx_htf >= ADX_TREND_LEVEL: # 추세장 (최대 5개)
        if current_position_count >= 5: return False
        is_long = signal_type.upper() in ["BUY", "LONG"]
        if is_long and target_price < ema_htf: return False
        if not is_long and target_price > ema_htf: return False
        return True
        
    return False
