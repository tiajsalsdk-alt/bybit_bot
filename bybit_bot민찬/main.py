import os
import sys
import time
import asyncio
import logging
import datetime as dt

os.system("")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from market_data import fetch_candles, get_balance, get_session, api_call
from scanner import scan_top_symbols
from indicators import add_indicators
from regime import detect_regime, Regime
from strategies import get_signal
from order_manager import (
    calc_qty, set_leverage,
    calc_tp_sl, place_hybrid_order,
    update_sl, close_position_market, monitor_positions,
    cancel_all_active_orders, close_all_active_positions,
    check_trade_approval, handle_trade_result, check_cooldown
)

from position_manager import (
    get_open_positions,
    check_regime_conflict, record_entry, _entry_regimes
)
from config import (
    TF_REGIME, TF_ENTRY, CANDLES_NEEDED, DAILY_LOSS_LIMIT,
    ADX_TREND_LEVEL, ADX_SIDEWAYS_LEVEL, DYNAMIC_MAX_TREND, DYNAMIC_MAX_SIDE,
    MAIN_LEV, MAX_SAME_DIR, MAX_CONSECUTIVE_ERRORS, BACKOFF_SECONDS
)

# ── 색상 및 아이콘 ──────────────────────────────────────────
G, R, Y, C, W = "\033[92m", "\033[91m", "\033[93m", "\033[96m", "\033[97m"
DIM, B, RST = "\033[2m", "\033[1m", "\033[0m"

REGIME_ICON = {
    Regime.UPTREND:       f"{G}^ UP  {RST}",
    Regime.DOWNTREND:     f"{R}v DOWN{RST}",
    Regime.SIDEWAYS_UP:   f"{C}~ S.UP{RST}",
    Regime.SIDEWAYS_DOWN: f"{Y}~ S.DN{RST}",
    Regime.DEADZONE:      f"{DIM}# WAIT{RST}",
}

CANDLE_BUFFER = 2      


class ColorFormatter(logging.Formatter):
    LEVEL_COLOR = {
        logging.DEBUG:    DIM,
        logging.INFO:     W,
        logging.WARNING:  Y,
        logging.ERROR:    R,
        logging.CRITICAL: B + R,
    }

    def format(self, record):
        color = self.LEVEL_COLOR.get(record.levelno, W)
        ts    = dt.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = f"{DIM}[{record.levelname[0]}]{RST}"
        msg   = f"{color}{record.getMessage()}{RST}"
        return f"{DIM}{ts}{RST} {level} {msg}"


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ColorFormatter())
    root.addHandler(console)
    file_h = logging.FileHandler("bot.log", encoding="utf-8")
    file_h.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root.addHandler(file_h)


def print_banner():
    from config import DEMO
    mode_text = "DEMO MODE" if DEMO else "REAL MODE"
    print(f"\n{C}{B}==========================================\n"
          f"    BYBIT AUTO TRADING BOT\n"
          f"           {mode_text}\n"
          f"=========================================={RST}\n")


def print_signal(side: str, symbol: str, engine: str, lev: int, qty: float,
                 entry: float, tp: float, sl: float):
    color = G if side == "Buy" else R
    arrow = "▲" if side == "Buy" else "▼"
    print(f"\n  {color}{B}{arrow} [{engine}] {symbol} {side}{RST}  "
          f"레버 {lev}x  수량 {qty:.4f}\n"
          f"  진입 {entry:.4f}  TP {tp:.4f}  SL {sl:.4f}\n")


def print_status(wallet: float, positions: list, regimes_dict: dict, current_max: int):
    now_str   = dt.datetime.now().strftime("%H:%M:%S")
    pos_color = G if len(positions) < current_max else R
    print(f"\n{DIM}{'─' * 50}{RST}")
    print(f"  {C}{B}{now_str}{RST}  "
          f"잔고 {W}{B}{wallet:>10,.0f} USDT{RST}  "
          f"포지션 {pos_color}{B}{len(positions)}/{current_max}{RST}")
    if regimes_dict:
        row = "  "
        display_syms = list(regimes_dict.keys())[:5]
        for sym in display_syms:
            row += f"{DIM}{sym[:3]}{RST} {REGIME_ICON.get(regimes_dict[sym], '?')}  "
        print(row)
    print(f"{DIM}{'─' * 50}{RST}\n")


log = logging.getLogger(__name__)

watchlist: list[str] = []
regimes:   dict[str, Regime] = {}
current_max_positions = DYNAMIC_MAX_SIDE
symbol_adx = {}
symbol_ema = {}
symbol_df15 = {}
daily_start_balance: float = 0.0
last_trade_date: str = ""

def seconds_until_next_15m() -> float:
    now = dt.datetime.now()
    seconds_past_slot = (now.minute % 15) * 60 + now.second + now.microsecond / 1_000_000
    return 15 * 60 - seconds_past_slot


async def refresh_watchlist():
    global watchlist
    log.info("거래대금 최상위 20개 종목 스캔 중... (금/스테이블 제외)")
    try:
        watchlist = await scan_top_symbols()
    except Exception as e:
        log.error(f"스캔 오류: {e}")
        raise e


async def run_regime_update():
    global current_max_positions, symbol_adx, symbol_ema
    log.info("[15m] 장세 업데이트 및 개별 심볼 ADX/EMA 체크")

    try:
        btc_df = await fetch_candles("BTCUSDT", TF_REGIME, limit=CANDLES_NEEDED)
        btc_df = add_indicators(btc_df)
        market_adx = btc_df.iloc[-1]["adx"]
        current_max_positions = DYNAMIC_MAX_TREND if market_adx >= ADX_TREND_LEVEL else DYNAMIC_MAX_SIDE
    except Exception as e:
        log.error(f"시장 국면 판단 오류: {e}")
        raise e

    for symbol in watchlist:
        try:
            df = await fetch_candles(symbol, TF_REGIME, limit=CANDLES_NEEDED)
            if len(df) < CANDLES_NEEDED:
                continue
            df = add_indicators(df)
            regimes[symbol] = detect_regime(df)

            last_row = df.iloc[-1]
            symbol_adx[symbol]  = last_row["adx"]
            symbol_ema[symbol]  = last_row["ema50"]
            symbol_df15[symbol] = df

            log.info(f"  {symbol}  {REGIME_ICON.get(regimes[symbol], '').strip()} (ADX: {symbol_adx[symbol]:.1f})")
            await asyncio.sleep(0.4)
        except Exception as e:
            log.error(f"장세 오류 {symbol}: {e}")
            raise e


async def run_entry_check(wallet: float):
    global daily_start_balance, last_trade_date

    today = dt.datetime.now().strftime("%Y-%m-%d")
    if last_trade_date != today:
        daily_start_balance = wallet
        last_trade_date = today
        log.info(f"[일일 시작] 기준 잔고: {wallet:,.0f} USDT")

    if daily_start_balance > 0 and wallet < daily_start_balance * (1 - DAILY_LOSS_LIMIT):
        loss_pct = (1 - wallet / daily_start_balance) * 100
        log.critical(f"🚨 [일일 손실 한도] 오늘 -{loss_pct:.1f}% 손실. 신규 진입 중단.")
        return

    positions = await get_open_positions()
    print_status(wallet, positions, regimes, current_max_positions)

    if len(positions) >= current_max_positions:
        return

    for symbol in watchlist:
        try:
            if any(p["symbol"] == symbol for p in positions):
                continue
            
            await cancel_all_active_orders(symbol)

            df_entry = await fetch_candles(symbol, TF_ENTRY, limit=CANDLES_NEEDED)
            if len(df_entry) < CANDLES_NEEDED:
                continue
            df_entry = add_indicators(df_entry)

            if 'atr' not in df_entry.columns:
                await asyncio.sleep(0.4)
                continue

            adx_htf = df_entry.iloc[-1].get("adx", 0.0)
            ema_htf = df_entry.iloc[-1].get("ema50", 0.0)
            if adx_htf == 0.0 or ema_htf == 0.0:
                await asyncio.sleep(0.4)
                continue

            signal = get_signal(df_entry, adx_htf, ema_htf, symbol,
                                df_htf=symbol_df15.get(symbol))
            if signal is None:
                await asyncio.sleep(0.4)
                continue

            side, engine_name, entry_price, sl_price = signal
            atr = df_entry.iloc[-1]["atr"]

            same_dir = sum(1 for p in positions if p["side"] == side)
            if same_dir >= MAX_SAME_DIR:
                await asyncio.sleep(0.4)
                continue

            approved = check_trade_approval(
                signal_type=side,
                current_price=entry_price,
                adx_htf=adx_htf,
                ema_htf=ema_htf,
                current_position_count=len(positions),
            )
            if not approved:
                await asyncio.sleep(0.4)
                continue

            qty = await calc_qty(symbol, entry_price, wallet)
            if qty <= 0:
                await asyncio.sleep(0.4)
                continue

            tp, sl = calc_tp_sl(symbol, side, entry_price, atr, sl_price)
            print_signal(side, symbol, engine_name, MAIN_LEV, qty, entry_price, tp, sl)
            
            ok = await place_hybrid_order(
                symbol=symbol, side=side, qty=qty,
                entry_price=entry_price, tp_price=tp, sl_price=sl,
            )
            if ok:
                regime_val = regimes.get(symbol, Regime.DEADZONE).value
                record_entry(symbol, regime_val)
                positions.append({"symbol": symbol, "side": side, "size": "1"})
                if len(positions) >= current_max_positions:
                    break

        except Exception as e:
            log.error(f"{symbol} 진입 체크 오류: {e}")
            raise e
        
        await asyncio.sleep(0.4)


async def main():
    setup_logging()
    print_banner()
    log.info("🚀 봇 가동 시작 (AWS 무한루프 방어 로직 적용 완료)")

    consecutive_errors = 0

    while True:
        try:
            # 1. 초기 정보 획득
            wallet = await get_balance()
            await refresh_watchlist()
            await run_regime_update()
            
            last_positions_symbols = set()
            loop_count = 0
            
            # 메인 실행 루프
            while True:
                wallet = await get_balance()
                positions = await get_open_positions()
                current_symbols = {p["symbol"] for p in positions}

                # 종료된 포지션 정산 처리
                closed_symbols = last_positions_symbols - current_symbols
                if closed_symbols:
                    session = get_session()
                    for sym in closed_symbols:
                        try:
                            resp = await api_call(session.get_closed_pnl, category="linear", symbol=sym, limit=1)
                            if resp and resp.get("retCode") == 0 and resp["result"]["list"]:
                                pnl_data = resp["result"]["list"][0]
                                closed_pnl = float(pnl_data["closedPnl"])
                                handle_trade_result(closed_pnl > 0)
                        except: pass

                last_positions_symbols = current_symbols

                # 15분 정각 대기 로직
                wait = seconds_until_next_15m()
                next_time = (dt.datetime.now() + dt.timedelta(seconds=wait)).strftime("%H:%M:%S")
                log.info(f"대기 중 — 다음 15분 정각: {next_time} ({wait:.0f}초 후)")

                check_interval = 10
                elapsed = 0
                while elapsed < wait:
                    await asyncio.sleep(min(check_interval, wait - elapsed))
                    elapsed += check_interval
                    # 포지션 모니터링
                    curr_pos = await get_open_positions()
                    if curr_pos:
                        await monitor_positions(curr_pos, _entry_regimes)

                await asyncio.sleep(CANDLE_BUFFER)

                # 매 1시간마다 종목 스캔 갱신
                if loop_count % 4 == 0:
                    await refresh_watchlist()
                
                await run_regime_update()
                await run_entry_check(wallet)
                
                # 성공 시 에러 카운터 초기화
                consecutive_errors = 0
                loop_count += 1

        except (asyncio.TimeoutError, ConnectionError, Exception) as e:
            err_msg = str(e)
            consecutive_errors += 1
            
            # API Rate Limit (10006) 또는 네트워크 에러 팩트 체크
            is_critical = any(kw in err_msg for kw in ["10006", "ReadTimeout", "ConnectionError", "Rate limit"])
            
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                log.critical(f"❌ Critical Error: 연속 {consecutive_errors}회 에러 발생. 킬-스위치 작동.")
                sys.exit(f"Critical Error: 강제 종료 (AWS 무한루프 차단) - {err_msg}")

            wait_time = BACKOFF_SECONDS[min(consecutive_errors-1, len(BACKOFF_SECONDS)-1)]
            log.error(f"🔥 에러 발생 ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {err_msg}")
            log.warning(f"⚠️ {wait_time}초 후 Exponential Backoff 재시도...")
            
            await asyncio.sleep(wait_time)
            continue


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("사용자에 의해 봇이 중단되었습니다.")
        sys.exit(0)
