import math
import time
import asyncio
import logging
import json
import os
from market_data import get_session, fetch_candles, api_call, get_balance
from indicators import add_indicators
from config import (
    MAIN_LEV, WEIGHT_SIDEWAYS, WEIGHT_TREND,
    ATR_SL_MULT_TREND, TREND_TP1_R_MULT, TREND_TP2_R_MULT, ATR_SL_MULT_SIDEWAYS,
    FEE_BUFFER, LIMIT_TIMEOUT_MINS, SIDEWAYS_TF, TREND_TF,
    ADX_SIDEWAYS_LEVEL, ADX_TREND_LEVEL, BB_LEN, BB_STD,
    COOLDOWN_FILE, SL_COOLDOWN_MINS
)

log = logging.getLogger(__name__)

# [V3.5] 물리적 쿨다운 기록 함수 (JSON 파일 기반)
def set_physical_lock(symbol: str):
    unlock_time = time.time() + (SL_COOLDOWN_MINS * 60)
    data = {}
    if os.path.exists(COOLDOWN_FILE):
        try:
            with open(COOLDOWN_FILE, 'r') as f: data = json.load(f)
        except: data = {}
    data[symbol] = unlock_time
    with open(COOLDOWN_FILE, 'w') as f: json.dump(data, f)
    log.critical(f"⚠️ [{symbol}] 손절 감지! 물리적 락 작동 ({SL_COOLDOWN_MINS}분간 진입 금지)")

# [V3.5] 물리적 락 상태 확인 함수
def is_physically_locked(symbol: str) -> bool:
    if not os.path.exists(COOLDOWN_FILE): return False
    try:
        with open(COOLDOWN_FILE, 'r') as f: data = json.load(f)
        unlock_time = data.get(symbol, 0)
        if time.time() < unlock_time:
            rem = int((unlock_time - time.time()) / 60)
            log.warning(f"  [Lock] {symbol} 진입 차단 중 ({rem}분 남음)")
            return True
    except: return False
    return False
_half_tp_done = set()
_entry_atr = {}      
_instrument_info_cache = {}
_entry_times = {}    
_active_limit_orders = {} # 미체결 주문 추적용

async def get_instrument_info(symbol: str):
    if symbol in _instrument_info_cache: return _instrument_info_cache[symbol]
    resp = await api_call(get_session().get_instruments_info, category="linear", symbol=symbol)
    if not resp or resp.get("retCode") != 0: return {"tick_size": 0.0001, "qty_step": 0.01, "min_qty": 0.01}
    info = resp["result"]["list"][0]
    data = {"tick_size": float(info["priceFilter"]["tickSize"]), "qty_step": float(info["lotSizeFilter"]["qtyStep"]), "min_qty": float(info["lotSizeFilter"]["minOrderQty"])}
    _instrument_info_cache[symbol] = data
    return data

def format_precision(value: float, step: float, is_floor: bool = False) -> str:
    if not value or step <= 0: return "0"
    p = len(str(float(step)).rstrip('0').split('.')[-1]) if '.' in str(float(step)) else 0
    if is_floor:
        m = 10 ** p
        v = math.floor(round(value * m, 10)) / m
    else:
        v = round(round(value / step) * step, p)
    return f"{v:.{p}f}"

async def calc_qty(symbol: str, entry_price: float, engine_name: str) -> float:
    wallet_usdt = await get_balance()
    weight = WEIGHT_TREND if "TREND" in engine_name else WEIGHT_SIDEWAYS
    margin = wallet_usdt * weight
    
    info = await get_instrument_info(symbol)
    raw_qty = (margin * MAIN_LEV) / entry_price
    return float(format_precision(raw_qty, info["qty_step"], is_floor=True))

def calc_tp_sl(symbol: str, side: str, entry_p: float, atr: float, engine_name: str, sl_p: float = 0.0):
    if "SIDEWAYS" in engine_name:
        sl = entry_p - (atr * ATR_SL_MULT_SIDEWAYS) if side == "Buy" else entry_p + (atr * ATR_SL_MULT_SIDEWAYS)
        tp = entry_p + (atr * 2.0) 
    else:
        # [V3.3] FVG R-Multiple Dynamic Target (Structural SL 기반)
        sl = sl_p if sl_p > 0 else (entry_p - (atr * ATR_SL_MULT_TREND) if side == "Buy" else entry_p + (atr * ATR_SL_MULT_TREND))
        risk = abs(entry_p - sl)
        tp = entry_p + (risk * TREND_TP2_R_MULT) if side == "Buy" else entry_p - (risk * TREND_TP2_R_MULT)
    return tp, sl

async def place_hybrid_order(symbol: str, side: str, qty: float, entry_price: float, tp_price: float, sl_price: float, engine_name: str, ma20_target: float = 0.0) -> bool:
    session = get_session(); info = await get_instrument_info(symbol)
    tick, step = info["tick_size"], info["qty_step"]
    qty_s, entry_s = format_precision(qty, step, True), format_precision(entry_price, tick)

    try:
        # [V3.1 핵심 수정] timeInForce="PostOnly" 강제 적용하여 시장가 추격(FOMO) 원천 차단
        resp = await api_call(session.place_order, 
                             category="linear", symbol=symbol, side=side, 
                             orderType="Limit", price=entry_s, qty=qty_s, positionIdx=0, 
                             timeInForce="PostOnly", 
                             takeProfit=format_precision(tp_price, tick), stopLoss=format_precision(sl_price, tick),
                             tpTriggerBy="LastPrice", slTriggerBy="LastPrice")
        
        if resp and resp.get("retCode") == 0:
            log.info(f"🎯 [{symbol}] {engine_name} 100% Full-Fill 지정가 매복(Post-Only) 완료 (@{entry_s}).")
            
            _active_limit_orders[symbol] = {
                "time": time.time(),
                "engine": engine_name,
                "ma20_target": ma20_target,
                "side": side,
                "qty": qty_s,
                "entry": entry_price
            }
            return True
        elif resp and resp.get("retCode") == 110007:
            log.error(f"❌ [{symbol}] 잔고 부족으로 주문 실패")
    except Exception as e: log.error(f"주문 에러: {e}")
    return False

async def monitor_positions(positions: list, entry_regimes: dict):
    session = get_session()
    current_positions = {p["symbol"] for p in positions}
    
    # 미체결 지정가 주문 25분 타임아웃 및 체결 감시
    for sym in list(_active_limit_orders.keys()):
        order_info = _active_limit_orders[sym]
        if sym in current_positions:
            log.info(f"✅ [{sym}] 지정가 매복 체결 확인! 익절 관리 모드 전환.")
            _entry_times[sym] = time.time()
            engine = order_info["engine"]
            
            # 횡보장일 경우 MA20 반익절 즉시 예약
            if "SIDEWAYS" in engine:
                info = await get_instrument_info(sym)
                half_qty = format_precision(float(order_info["qty"]) * 0.5, info["qty_step"], is_floor=True)
                await api_call(session.place_order, category="linear", symbol=sym, side="Sell" if order_info["side"]=="Buy" else "Buy",
                             orderType="Limit", price=format_precision(order_info["ma20_target"], info["tick_size"]), qty=half_qty, 
                             reduceOnly=True, postOnly=True, positionIdx=0)
            _active_limit_orders.pop(sym, None)
            continue
            
        if time.time() - order_info["time"] > (LIMIT_TIMEOUT_MINS * 60):
            await api_call(session.cancel_all_orders, category="linear", symbol=sym)
            _active_limit_orders.pop(sym, None)
            print(f"  [Cancel] {sym} - {LIMIT_TIMEOUT_MINS}분 미체결로 주문 취소 (FOMO 방지)")

    if not positions:
        _half_tp_done.clear()
        return
    
    active_symbols = {p["symbol"] for p in positions}
    for sym in list(_half_tp_done):
        if sym not in active_symbols: _half_tp_done.discard(sym)

    for pos in positions:
        sym = pos["symbol"]; side = pos["side"]; qty = float(pos["size"])
        entry_p = float(pos["avgPrice"]); curr_p = float(pos["markPrice"])
        engine = entry_regimes.get(sym, "")
        info = await get_instrument_info(sym); tick = info["tick_size"]
        atr = _entry_atr.get(sym, 0.0)

        # --- [1] 횡보장 동적 익절 관리 ---
        if "SIDEWAYS" in engine:
            try:
                df_15m = add_indicators(await fetch_candles(sym, SIDEWAYS_TF, limit=50))
                ma20 = df_15m['close'].rolling(window=20).mean().iloc[-1]
                std = df_15m['close'].rolling(window=20).std().iloc[-1]
                bb_target = ma20 + (std * BB_STD) if side == "Buy" else ma20 - (std * BB_STD)

                if sym not in _half_tp_done:
                    hit_ma20 = (curr_p >= ma20) if side == "Buy" else (curr_p <= ma20)
                    if hit_ma20:
                        be_price = entry_p * (1 + FEE_BUFFER) if side == "Buy" else entry_p * (1 - FEE_BUFFER)
                        await api_call(session.set_trading_stop, category="linear", symbol=sym, 
                                     stopLoss=format_precision(be_price, tick), positionIdx=0)
                        _half_tp_done.add(sym); log.critical(f"🚀 [{sym}] 횡보 1차 MA20 도달! 본절 SL 이동 완료.")
                else:
                    # 횡보장 최종 청산 또는 손절 감시
                    is_sl_hit = (curr_p <= float(pos["stopLoss"])) if side == "Buy" else (curr_p >= float(pos["stopLoss"]))
                    if is_sl_hit:
                        set_physical_lock(sym)
                    
                    await api_call(session.set_trading_stop, category="linear", symbol=sym, 
                                 takeProfit=format_precision(bb_target, tick), tpTriggerBy="LastPrice", positionIdx=0)
            except: continue

        # --- [2] 추세장 (TREND_FVG) 1.2R 반익반본 및 2.0R 최종 익절 ---
        elif engine == "TREND_FVG":
            if sym not in _half_tp_done:
                # [V3.3] 실시간 리스크(Risk) 기반 1.2R 도달 체크
                current_sl = float(pos["stopLoss"])
                risk = abs(entry_p - current_sl)
                
                hit_1st_tp = (curr_p >= entry_p + (risk * TREND_TP1_R_MULT)) if side == "Buy" else (curr_p <= entry_p - (risk * TREND_TP1_R_MULT))
                
                if hit_1st_tp:
                    half_qty = format_precision(qty * 0.5, info["qty_step"], is_floor=True)
                    if float(half_qty) >= info["min_qty"]:
                        log.critical(f"🚀 [{sym}] 추세장 {TREND_TP1_R_MULT}R 도달! 50% 지정가 익절 및 본절 이동.")
                        close_side = "Sell" if side == "Buy" else "Buy"
                        await api_call(session.place_order, category="linear", symbol=sym, side=close_side,
                                     orderType="Limit", price=format_precision(curr_p, tick), 
                                     qty=half_qty, reduceOnly=True, postOnly=True, positionIdx=0)
                        be_price = entry_p * (1 + FEE_BUFFER) if side == "Buy" else entry_p * (1 - FEE_BUFFER)
                        await api_call(session.set_trading_stop, category="linear", symbol=sym, 
                                     stopLoss=format_precision(be_price, tick), positionIdx=0)
                        _half_tp_done.add(sym)
            
            # [V3.5] 추세장 손절 감시
            is_sl_hit = (curr_p <= float(pos["stopLoss"])) if side == "Buy" else (curr_p >= float(pos["stopLoss"]))
            if is_sl_hit:
                set_physical_lock(sym)

async def close_position_market(symbol: str, side: str, qty: float):
    close_side = "Sell" if side == "Buy" else "Buy"
    await api_call(get_session().place_order, category="linear", symbol=symbol, side=close_side, orderType="Market", qty=str(qty), reduceOnly=True, positionIdx=0)

def check_trade_approval(side, price, adx, ema, count):
    if adx >= ADX_TREND_LEVEL:
        if count >= 5: return False
        return (side == "Buy" and price > ema) or (side == "Sell" and price < ema)
    return count < 3 if adx < ADX_TREND_LEVEL else False

async def cancel_all_active_orders(s): await api_call(get_session().cancel_all_orders, category="linear", symbol=s)
async def get_open_positions():
    resp = await api_call(get_session().get_positions, category="linear", settleCoin="USDT")
    if not resp or resp.get("retCode") != 0: return []
    return [p for p in resp["result"]["list"] if float(p["size"]) > 0]
