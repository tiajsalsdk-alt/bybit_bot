import math
import time
import asyncio
import logging
from market_data import get_session, fetch_candles, api_call
from notifier import send
from indicators import add_indicators
from config import (
    MAIN_LEV, ALT_LEV,
    SIDEWAYS_PRO_PCT, SIDEWAYS_ANTI_PCT, TREND_SEED_PCT,
    LIMIT_TIMEOUT, ATR_SL_MULT_TREND, ATR_TP_MULT_TREND, ATR_SL_MULT_SIDEWAYS,
    ROE_TAKE_PROFIT_PCT, TREND_HALF_TP_ROE, TF_ENTRY, BB_LEN, BB_STD
)

log = logging.getLogger(__name__)

# --- [상태 관리 변수] ---
_half_tp_done = set()
_entry_atr = {}      # symbol -> entry_time_atr
_instrument_info_cache = {}
_entry_times = {}    # symbol -> entry_timestamp
consecutive_losses = 0
cooldown_until = 0.0

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
        log.error(f"[{symbol}] 정보 획득 실패: {e}")
    return {"tick_size": 0.0001, "qty_step": 0.01, "min_qty": 0.01}

def round_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0: return price
    return round(round(price / tick_size) * tick_size, 8)

def get_leverage(symbol: str) -> int:
    return MAIN_LEV

async def set_leverage(symbol: str, leverage: int) -> bool:
    try:
        session = get_session()
        resp = await api_call(session.set_leverage, category="linear", symbol=symbol,
                        buyLeverage=str(leverage), sellLeverage=str(leverage))
        if resp and resp.get("retCode") in [0, 110043]:
            return True
        log.error(f"[{symbol}] 레버리지 설정 실패: {resp.get('retMsg')}")
    except Exception as e:
        log.error(f"[{symbol}] 레버리지 설정 중 예외: {e}")
    return False

async def update_sl(symbol: str, new_sl: float):
    try:
        session = get_session()
        await api_call(session.set_trading_stop, category="linear", symbol=symbol,
                 stopLoss=str(new_sl), slTriggerBy="LastPrice", positionIdx=0)
        return True
    except: return False

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

def check_cooldown():
    global consecutive_losses, cooldown_until
    if time.time() < cooldown_until:
        return True
    if consecutive_losses >= 3:
        consecutive_losses = 0 
    return False

async def calc_qty(symbol: str, entry_price: float, wallet_usdt: float, engine_name: str) -> float:
    if engine_name == "SIDEWAYS_PRO":
        seed_pct = SIDEWAYS_PRO_PCT
    elif engine_name == "SIDEWAYS_ANTI":
        seed_pct = SIDEWAYS_ANTI_PCT
    else:
        seed_pct = TREND_SEED_PCT

    margin = wallet_usdt * seed_pct
    if margin > wallet_usdt * 0.95:
        log.warning(f"[{symbol}] 가용 잔고 부족 (필요: {margin:.1f}, 잔고: {wallet_usdt:.1f})")
        return 0.0
    leverage = MAIN_LEV
    notional = margin * leverage
    raw_qty = notional / entry_price
    info = await get_instrument_info(symbol)
    qty = math.floor(raw_qty / info["qty_step"]) * info["qty_step"]
    if qty < info["min_qty"]: return 0.0
    return round(qty, 8)

def calc_tp_sl(symbol: str, side: str, entry_p: float, atr: float, engine_name: str):
    if "SIDEWAYS" in engine_name:
        # [수정] SHORT(Sell) 포지션 TP/SL 방향성 정정 (10001 에러 방지)
        if side == "Buy":
            sl = entry_p - (atr * ATR_SL_MULT_SIDEWAYS)
            tp = entry_p * (1 + ROE_TAKE_PROFIT_PCT) 
        else:
            sl = entry_p + (atr * ATR_SL_MULT_SIDEWAYS)
            tp = entry_p * (1 - ROE_TAKE_PROFIT_PCT)
    else:
        # TREND 로직: 기존 역방향 연산 유지 (Buy: TP+, SL- / Sell: TP-, SL+)
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
            if order_list:
                return order_list[0]["orderStatus"] == "Filled"
    except: pass
    return False

async def place_hybrid_order(symbol: str, side: str, qty: float, entry_price: float, tp_price: float, sl_price: float, is_entry: bool = True) -> bool:
    if not await set_leverage(symbol, MAIN_LEV): return False
    session = get_session()
    if is_entry:
        try:
            ticker_resp = await api_call(session.get_tickers, category="linear", symbol=symbol)
            if ticker_resp and ticker_resp.get("retCode") == 0:
                ticker_info = ticker_resp["result"]["list"][0]
                ask = float(ticker_info["ask1Price"]); bid = float(ticker_info["bid1Price"])
                spread_pct = (ask - bid) / bid
                if spread_pct > 0.001:
                    log.warning(f"🚫 [{symbol}] 스프레드 초과 ({spread_pct*100:.3f}%).")
                    return False
        except: return False

    info = await get_instrument_info(symbol)
    tick = info["tick_size"]
    wait_time = 5 if is_entry else 2
    entry_s = str(round_to_tick(entry_price, tick))
    tp_s = str(round_to_tick(tp_price, tick)); sl_s = str(round_to_tick(sl_price, tick))

    common = dict(
        category="linear", symbol=symbol, side=side, qty=str(qty),
        takeProfit=tp_s, stopLoss=sl_s, tpOrderType="Market", slOrderType="Market",
        tpTriggerBy="LastPrice", slTriggerBy="LastPrice", positionIdx=0,
        timeInForce="GTC", tpslMode="Full"
    )

    try:
        resp = await api_call(session.place_order, **common, orderType="Limit", price=entry_s)
        if not resp or resp["retCode"] != 0: return False
        order_id = resp["result"]["orderId"]
        await asyncio.sleep(wait_time)
        
        check = await api_call(session.get_open_orders, category="linear", symbol=symbol, orderId=order_id)
        active_orders = check.get("result", {}).get("list", []) if check else []
        if not active_orders:
            if await is_order_filled(symbol, order_id):
                log.info(f"✅ {side} {symbol} 체결 (지정가)")
                if is_entry: _entry_times[symbol] = time.time()
                return True
            return False

        await api_call(session.cancel_order, category="linear", symbol=symbol, orderId=order_id)
        resp2 = await api_call(session.place_order, **common, orderType="Market")
        if resp2 and resp2["retCode"] == 0:
            log.info(f"✅ {side} {symbol} 체결 (시장가)")
            if is_entry: _entry_times[symbol] = time.time()
            return True
    except: pass
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

        # [0] 타임 스탑 (12캔들 - 5분봉 기준 60분)
        entry_ts = _entry_times.get(sym)
        if entry_ts and (time.time() - entry_ts > 3600):
            log.warning(f"⏰ [{sym}] 타임 스탑 청산 (12캔들 경과)")
            await close_position_market(sym, side, qty)
            _entry_times.pop(sym, None); continue

        # [명세서 3] 동적 트레일링 스탑 (반익절 이후 작동)
        if sym in _half_tp_done:
            try:
                hist_df = await fetch_candles(sym, TF_ENTRY, limit=5)
                prev_row = hist_df.iloc[-2]
                if side == "Buy" and current_p < prev_row["low"]:
                    log.critical(f"📉 [{sym}] 동적 트레일링 스탑 (직전 저점 이탈)!")
                    await close_position_market(sym, side, qty); continue
                elif side == "Sell" and current_p > prev_row["high"]:
                    log.critical(f"📈 [{sym}] 동적 트레일링 스탑 (직전 고점 돌파)!")
                    await close_position_market(sym, side, qty); continue
            except: pass

        roe = ((current_p - entry_p) / entry_p if side == "Buy" else (entry_p - current_p) / entry_p) * lev

        # --- [로직 2: 볼벤 스캘핑 청산] ---
        if "SIDEWAYS" in engine:
            try:
                df = await fetch_candles(sym, TF_ENTRY, limit=25)
                ma = df['close'].rolling(window=20).mean().iloc[-1]
                bb_up = ma + (df['close'].rolling(window=20).std().iloc[-1] * BB_STD)
                bb_dn = ma - (df['close'].rolling(window=20).std().iloc[-1] * BB_STD)
                
                hit_mid = (current_p >= ma) if side == "Buy" else (current_p <= ma)
                if sym not in _half_tp_done and hit_mid:
                    info = await get_instrument_info(sym)
                    half_qty = math.floor((qty * 0.5) / info["qty_step"]) * info["qty_step"]
                    if half_qty >= info["min_qty"]:
                        log.critical(f"🚀 [{sym}] 로직2: 볼벤 중앙선 반익절 (시장가)!")
                        close_side = "Sell" if side == "Buy" else "Buy"
                        await api_call(get_session().place_order, category="linear", symbol=sym, side=close_side,
                                 orderType="Market", qty=str(half_qty), reduceOnly=True, positionIdx=0)
                        await update_sl(sym, entry_p); _half_tp_done.add(sym)
                    continue
                hit_target = (current_p >= bb_up) if side == "Buy" else (current_p <= bb_dn)
                if hit_target:
                    log.critical(f"💰 [{sym}] 로직2: 목표가 도달 전량 익절!")
                    await close_position_market(sym, side, qty); continue
            except: pass
            continue

        # --- [로직 1: SMC Sniper 청산] ---
        if sym not in _half_tp_done and roe >= TREND_HALF_TP_ROE:
            info = await get_instrument_info(sym)
            half_qty = math.floor((qty * 0.5) / info["qty_step"]) * info["qty_step"]
            if half_qty >= info["min_qty"]:
                log.critical(f"🚀 [{sym}] SMC ROE {TREND_HALF_TP_ROE*100:.0f}% 반익절 (지정가)!")
                close_side = "Sell" if side == "Buy" else "Buy"
                tp_p = entry_p * 1.01 if side == "Buy" else entry_p * 0.99
                await api_call(get_session().place_order, category="linear", symbol=sym, side=close_side,
                         orderType="Limit", price=str(round_to_tick(tp_p, info["tick_size"])), 
                         qty=str(half_qty), reduceOnly=True, positionIdx=0)
                await update_sl(sym, entry_p); _half_tp_done.add(sym)

async def close_all_active_positions():
    try:
        session = get_session()
        resp = await api_call(session.get_positions, category="linear", settleCoin="USDT")
        if resp and resp.get("retCode") == 0:
            for pos in resp["result"]["list"]:
                if float(pos["size"]) > 0:
                    await close_position_market(pos["symbol"], pos["side"], float(pos["size"]))
    except: pass

async def cancel_all_active_orders(symbol: str):
    try:
        session = get_session()
        open_orders = await api_call(session.get_open_orders, category="linear", symbol=symbol)
        if open_orders and open_orders.get("retCode") == 0 and open_orders["result"]["list"]:
            await api_call(session.cancel_all_orders, category="linear", symbol=symbol)
            return True
    except: pass
    return False

async def close_position_market(symbol: str, side: str, qty: float):
    close_side = "Sell" if side == "Buy" else "Buy"
    try:
        await api_call(get_session().place_order, category="linear", symbol=symbol, side=close_side,
                 orderType="Market", qty=str(qty), reduceOnly=True, positionIdx=0, timeInForce="IOC")
        await cancel_all_active_orders(symbol)
    except: pass

def check_trade_approval(signal_type, current_price, adx_htf, ema_htf, current_position_count):
    if check_cooldown():
        log.warning("❄️ [COOL DOWN] 쿨다운 중.")
        return False
    # [수정] ADX 데드존 확장: 22 ~ 25
    if 22 < adx_htf < 25: return False
    if adx_htf <= 22: return current_position_count < 3
    if adx_htf >= 25:
        if current_position_count >= 5: return False
        is_long = signal_type.upper() in ["BUY", "LONG"]
        if current_price > ema_htf and is_long: return True
        if current_price < ema_htf and not is_long: return True
    return False
