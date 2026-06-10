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
    MAIN_LEV, TREND_SEED_PCT, LIMIT_TIMEOUT,
    USE_TP1, TREND_TP1_R_MULT, TREND_TP2_R_MULT,
    TF_ENTRY, ADX_TREND, ADX_SIDEWAYS,
    DON_TIME_STOP_BARS, DYNAMIC_MAX_TREND
)

log = logging.getLogger(__name__)

COOLDOWN_FILE = "cooldowns.json"
_half_tp_done   = set()
_smart_tp_armed = set()   # TP_HALF(1.1R) 도달 후 스마트 청산 활성화
_entry_atr      = {}
_entry_risk     = {}   # symbol -> abs(entry - sl), FVG R-Multiple 계산용
_instrument_info_cache = {}
_entry_times  = {}

def save_cooldown_data(losses, until):
    try:
        with open(COOLDOWN_FILE, "w") as f:
            json.dump({"consecutive_losses": losses, "cooldown_until": until}, f)
    except Exception as e:
        log.error(f"쿨다운 저장 실패: {e}")

def load_cooldown_data():
    try:
        if os.path.exists(COOLDOWN_FILE):
            with open(COOLDOWN_FILE, "r") as f:
                data = json.load(f)
                return data.get("consecutive_losses", 0), data.get("cooldown_until", 0.0)
    except Exception as e:
        log.error(f"쿨다운 로드 실패: {e}")
    return 0, 0.0

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
                "qty_step":  float(info["lotSizeFilter"]["qtyStep"]),
                "min_qty":   float(info["lotSizeFilter"]["minOrderQty"])
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
    for i in range(3):
        try:
            session = get_session()
            resp = await api_call(session.set_trading_stop, category="linear", symbol=symbol,
                                  stopLoss=str(new_sl), slTriggerBy="LastPrice", positionIdx=0)
            if resp and resp.get("retCode") == 0:
                return True
            log.error(f"[{symbol}] SL 업데이트 실패 ({i+1}/3): {resp.get('retMsg')}")
        except Exception as e:
            if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
            log.error(f"[{symbol}] SL 업데이트 예외 ({i+1}/3): {e}")
        await asyncio.sleep(1)
    log.critical(f"🚨 [{symbol}] 손절선 설정 최종 실패!")
    return False


def handle_trade_result(is_win: bool):
    global consecutive_losses, cooldown_until
    if is_win:
        consecutive_losses = 0
        log.info("🎯 익절 성공! 연속 손절 카운트 초기화.")
    else:
        # 이미 쿨다운 중이면 카운터 추가 증가 없음
        if time.time() < cooldown_until:
            log.warning(f"❌ 손절 발생 (쿨다운 진행 중, 카운트 동결 {consecutive_losses}/3)")
        else:
            consecutive_losses += 1
            log.warning(f"❌ 손절 발생 ({consecutive_losses}/3)")
            if consecutive_losses >= 3:
                cooldown_until = time.time() + 3600
                log.critical("🚨 [COOL DOWN] 3연속 손절! 1시간 거래 중단.")
    save_cooldown_data(consecutive_losses, cooldown_until)


def check_cooldown():
    global consecutive_losses, cooldown_until
    now = time.time()
    # 쿨다운이 만료됐으면 카운터 리셋
    if consecutive_losses >= 3 and now >= cooldown_until:
        consecutive_losses = 0
        cooldown_until = 0.0
        save_cooldown_data(0, 0.0)
        log.info("✅ [COOL DOWN] 쿨다운 종료 — 연속 손절 카운터 리셋")
        return False
    return now < cooldown_until


async def calc_qty(symbol: str, entry_price: float, current_wallet: float) -> float:
    try:
        wallet_usdt = await get_balance()
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
        log.error(f"[{symbol}] 잔고 조회 실패, 이전 값 사용: {e}")
        wallet_usdt = current_wallet

    margin = wallet_usdt * TREND_SEED_PCT
    if margin <= 0:
        log.warning(f"[{symbol}] 증거금 부족 ({margin:.2f})")
        return 0.0

    notional = margin * MAIN_LEV
    raw_qty  = notional / entry_price

    info = await get_instrument_info(symbol)
    qty  = math.floor(raw_qty / info["qty_step"]) * info["qty_step"]

    if qty < info["min_qty"]:
        log.warning(f"[{symbol}] 최소 수량 미달 (qty={qty}, min={info['min_qty']})")
        return 0.0
    return round(qty, 8)


def calc_tp_sl(symbol: str, side: str, entry_p: float, atr: float, sl_p: float = 0.0):
    # FVG 구조적 손절가가 있으면 R-Multiple, 없으면 ATR 기본값
    sl   = sl_p if sl_p > 0 else (entry_p - atr * 1.5 if side == "Buy" else entry_p + atr * 1.5)
    risk = abs(entry_p - sl)
    tp   = entry_p + risk * TREND_TP2_R_MULT if side == "Buy" else entry_p - risk * TREND_TP2_R_MULT
    _entry_atr[symbol]  = atr
    _entry_risk[symbol] = risk   # TP1 계산에 사용
    return tp, sl


async def is_order_filled(symbol: str, order_id: str) -> bool:
    try:
        session = get_session()
        resp = await api_call(session.get_order_history, category="linear",
                              symbol=symbol, orderId=order_id)
        if resp and resp.get("retCode") == 0:
            order_list = resp["result"]["list"]
            if order_list:
                return order_list[0]["orderStatus"] == "Filled"
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
    return False


async def verify_position_exists(symbol: str) -> bool:
    try:
        session = get_session()
        resp = await api_call(session.get_positions, category="linear", symbol=symbol)
        if resp and resp.get("retCode") == 0:
            for p in resp["result"]["list"]:
                if p["symbol"] == symbol and float(p.get("size", 0)) > 0:
                    return True
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
    return False


async def place_hybrid_order(symbol: str, side: str, qty: float,
                             entry_price: float, tp_price: float, sl_price: float,
                             is_entry: bool = True) -> bool:
    if not await set_leverage(symbol, MAIN_LEV):
        return False
    session  = get_session()

    if is_entry:
        try:
            ticker_resp = await api_call(session.get_tickers, category="linear", symbol=symbol)
            if ticker_resp and ticker_resp.get("retCode") == 0:
                t = ticker_resp["result"]["list"][0]
                ask = float(t["ask1Price"]); bid = float(t["bid1Price"])
                if (ask - bid) / bid > 0.001:
                    log.warning(f"🚫 [{symbol}] 스프레드 초과. 진입 취소.")
                    return False
        except Exception as e:
            if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e

    info     = await get_instrument_info(symbol)
    tick     = info["tick_size"]
    wait_t   = 4500 if is_entry else 2  # CE 대기: 5봉 × 15분 = 75분
    entry_s  = str(round_to_tick(entry_price, tick))
    tp_s     = str(round_to_tick(tp_price, tick))
    sl_s     = str(round_to_tick(sl_price, tick))

    common = dict(
        category="linear", symbol=symbol, side=side, qty=str(qty),
        takeProfit=tp_s, stopLoss=sl_s,
        tpOrderType="Market", slOrderType="Market",
        tpTriggerBy="LastPrice", slTriggerBy="LastPrice",
        positionIdx=0, timeInForce="PostOnly", tpslMode="Full"
    )

    try:
        resp = await api_call(session.place_order, **common, orderType="Limit", price=entry_s)
        if resp and resp.get("retCode") == 0:
            order_id = resp["result"]["orderId"]
            await asyncio.sleep(wait_t)
            check = await api_call(session.get_open_orders, category="linear",
                                   symbol=symbol, orderId=order_id)
            if not (check or {}).get("result", {}).get("list", []):
                if await is_order_filled(symbol, order_id):
                    log.info(f"✅ {side} {symbol} 체결 (PostOnly 지정가)")
                    if is_entry: _entry_times[symbol] = time.time()
                    return True
            cancel_resp = await api_call(session.cancel_order, category="linear",
                                         symbol=symbol, orderId=order_id)
            # 취소 성공 = 진짜 미체결 → 즉시 종료
            if cancel_resp and cancel_resp.get("retCode") == 0:
                log.info(f"⏭ [{symbol}] PostOnly 미체결 — 가격 이탈, 진입 취소")
                return False
            # 취소 실패(110001) = 주문이 이미 사라짐 → 체결 여부 재확인
            log.info(f"⏭ [{symbol}] 취소 실패(이미 소멸) — 포지션 재확인 중")
        else:
            log.info(f"⏭ [{symbol}] PostOnly 거절 (Taker 상황) — 진입 취소")
            return False

    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
        log.error(f"⚠️ [{symbol}] 주문 예외: {e}. 포지션 팩트체크.")

    if await verify_position_exists(symbol):
        log.info(f"🔍 [{symbol}] 포지션 체결 확인됨.")
        if is_entry: _entry_times[symbol] = time.time()
        return True
    return False


async def monitor_positions(positions: list, entry_regimes: dict):
    if not positions:
        _half_tp_done.clear()
        _smart_tp_armed.clear()
        return

    active_symbols = {p["symbol"] for p in positions}
    for sym in list(_half_tp_done):
        if sym not in active_symbols:
            _half_tp_done.discard(sym)
    for sym in list(_smart_tp_armed):
        if sym not in active_symbols:
            _smart_tp_armed.discard(sym)

    for pos in positions:
        sym       = pos["symbol"]
        side      = pos["side"]
        qty       = float(pos["size"])
        entry_p   = float(pos["avgPrice"])
        current_p = float(pos["markPrice"])

        # 타임스탑 (15시간)
        entry_ts = _entry_times.get(sym)
        if entry_ts and (time.time() - entry_ts > DON_TIME_STOP_BARS * 15 * 60):
            log.warning(f"⏰ [{sym}] 타임스탑 청산")
            await close_position_market(sym, side, qty)
            _entry_times.pop(sym, None)
            _smart_tp_armed.discard(sym)
            continue

        risk = _entry_risk.get(sym, 0)

        # 스마트 TP 아밍: TP_HALF(1.1R) 도달 시 활성화
        if sym not in _smart_tp_armed and risk > 0:
            tp_half = (entry_p + risk * TREND_TP1_R_MULT if side == "Buy"
                       else entry_p - risk * TREND_TP1_R_MULT)
            if (side == "Buy" and current_p >= tp_half) or (side == "Sell" and current_p <= tp_half):
                _smart_tp_armed.add(sym)
                log.info(f"🎯 [{sym}] 스마트TP 활성화 ({TREND_TP1_R_MULT}R 도달)")

        # 스마트 트레일링 스탑 (백테스트 동일 로직: TP_HALF 후 직전봉 극단 이탈 시 청산)
        if sym in _smart_tp_armed:
            try:
                hist_df  = await fetch_candles(sym, TF_ENTRY, limit=5)
                prev_row = hist_df.iloc[-2]
                if side == "Buy" and current_p < prev_row["low"]:
                    log.critical(f"📉 [{sym}] 스마트TP 청산 (직전 저점 이탈)")
                    await close_position_market(sym, side, qty)
                    _smart_tp_armed.discard(sym)
                    continue
                elif side == "Sell" and current_p > prev_row["high"]:
                    log.critical(f"📈 [{sym}] 스마트TP 청산 (직전 고점 돌파)")
                    await close_position_market(sym, side, qty)
                    _smart_tp_armed.discard(sym)
                    continue
            except Exception as e:
                if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e

        # 반익절 (USE_TP1=True일 때만 동작)
        if USE_TP1 and risk > 0:
            tp1_price = (entry_p + risk * TREND_TP1_R_MULT if side == "Buy"
                         else entry_p - risk * TREND_TP1_R_MULT)
            tp1_hit   = current_p >= tp1_price if side == "Buy" else current_p <= tp1_price
        else:
            tp1_hit = False
        if sym not in _half_tp_done and tp1_hit:
            info     = await get_instrument_info(sym)
            half_qty = math.floor((qty * 0.5) / info["qty_step"]) * info["qty_step"]
            if half_qty >= info["min_qty"]:
                log.critical(f"🚀 [{sym}] FVG TP1 {TREND_TP1_R_MULT}R 반익절!")
                close_side = "Sell" if side == "Buy" else "Buy"
                await api_call(get_session().place_order,
                               category="linear", symbol=sym, side=close_side,
                               orderType="Limit",
                               price=str(round_to_tick(tp1_price, info["tick_size"])),
                               qty=str(half_qty), reduceOnly=True, positionIdx=0)
                await update_sl(sym, entry_p)
                _half_tp_done.add(sym)


async def close_position_market(symbol: str, side: str, qty: float):
    close_side = "Sell" if side == "Buy" else "Buy"
    for i in range(3):
        try:
            resp = await api_call(get_session().place_order,
                                  category="linear", symbol=symbol, side=close_side,
                                  orderType="Market", qty=str(qty),
                                  reduceOnly=True, positionIdx=0, timeInForce="IOC")
            if resp and resp.get("retCode") == 0:
                log.info(f"✅ [{symbol}] 시장가 청산 성공.")
                await cancel_all_active_orders(symbol)
                return True
            log.error(f"❌ [{symbol}] 청산 실패 ({i+1}/3): {resp.get('retMsg')}")
        except Exception as e:
            if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
            log.error(f"⚠️ [{symbol}] 청산 예외 ({i+1}/3): {e}")
        await asyncio.sleep(2)
    log.critical(f"🚨 [{symbol}] 모든 청산 재시도 실패!")
    return False


async def cancel_all_active_orders(symbol: str):
    try:
        session     = get_session()
        open_orders = await api_call(session.get_open_orders,
                                     category="linear", symbol=symbol)
        if open_orders and open_orders.get("retCode") == 0 \
                and open_orders["result"]["list"]:
            await api_call(session.cancel_all_orders,
                           category="linear", symbol=symbol)
            return True
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
    return False


async def close_all_active_positions():
    try:
        session = get_session()
        resp = await api_call(session.get_positions,
                              category="linear", settleCoin="USDT")
        if resp and resp.get("retCode") == 0:
            for pos in resp["result"]["list"]:
                if float(pos["size"]) > 0:
                    await close_position_market(pos["symbol"], pos["side"],
                                               float(pos["size"]))
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e


def check_trade_approval(signal_type: str, current_price: float,
                         adx_htf: float, ema_htf: float,
                         current_position_count: int) -> bool:
    if check_cooldown():
        log.warning("❄️ [COOL DOWN] 쿨다운 중.")
        return False

    # ADX 데드존(22~35)은 진입 안 함
    if ADX_SIDEWAYS < adx_htf < ADX_TREND:
        return False

    # 횡보장 — 현재 사용 안 함
    if adx_htf <= ADX_SIDEWAYS:
        return False

    # 추세장 — DONCHIAN
    if adx_htf >= ADX_TREND:
        if current_position_count >= DYNAMIC_MAX_TREND:
            return False
        is_long = signal_type.upper() in ["BUY", "LONG"]
        if current_price > ema_htf and is_long:
            return True
        if current_price < ema_htf and not is_long:
            return True

    return False
