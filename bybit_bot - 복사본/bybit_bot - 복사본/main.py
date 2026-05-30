import os
import sys
import time
import asyncio
import random
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
    MAIN_LEV
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

def seconds_until_next_5m() -> float:
    now = dt.datetime.now()
    seconds_past_slot = (now.minute % 5) * 60 + now.second + now.microsecond / 1_000_000
    return 5 * 60 - seconds_past_slot


async def refresh_watchlist():
    global watchlist
    log.info("거래대금 최상위 20개 종목 스캔 중... (금/스테이블 제외)")
    try:
        watchlist = await scan_top_symbols()
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
        log.error(f"스캔 오류: {e}")


async def run_regime_update():
    global current_max_positions, symbol_adx, symbol_ema
    log.info("[15m] 장세 업데이트 및 개별 심볼 ADX/EMA 체크")
    
    try:
        btc_df = await fetch_candles("BTCUSDT", TF_REGIME, limit=CANDLES_NEEDED)
        btc_df = add_indicators(btc_df)
        market_adx = btc_df.iloc[-1]["adx"]
        if market_adx >= ADX_TREND_LEVEL:
            current_max_positions = DYNAMIC_MAX_TREND
        else:
            current_max_positions = DYNAMIC_MAX_SIDE
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
        log.error(f"시장 국면 판단 오류: {e}")

    for symbol in watchlist:
        try:
            df = await fetch_candles(symbol, TF_REGIME, limit=CANDLES_NEEDED)
            if len(df) < CANDLES_NEEDED:
                continue
            df = add_indicators(df)
            regimes[symbol] = detect_regime(df)
            
            last_row = df.iloc[-1]
            symbol_adx[symbol] = last_row["adx"]
            symbol_ema[symbol] = last_row["ema200"]
            
            log.info(f"  {symbol}  {REGIME_ICON.get(regimes[symbol], '').strip()} (ADX: {symbol_adx[symbol]:.1f})")
            
            await asyncio.sleep(0.4)
        except Exception as e:
            if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
            log.error(f"장세 오류 {symbol}: {e}")


async def run_entry_check(wallet: float):
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

            required_cols = ['bw', 'd_low', 'stoch_d', 'ema20']
            if not all(col in df_entry.columns for col in required_cols):
                await asyncio.sleep(0.4)
                continue

            adx_htf = symbol_adx.get(symbol, 0.0)
            ema_htf  = symbol_ema.get(symbol, 0.0)
            if adx_htf == 0.0 or ema_htf == 0.0:
                await asyncio.sleep(0.4)
                continue

            signal = get_signal(df_entry, adx_htf, ema_htf, symbol)
            if signal is None:
                await asyncio.sleep(0.4)
                continue

            side, engine_name = signal
            entry_price = df_entry.iloc[-1]["close"]
            atr         = df_entry.iloc[-1]["atr"]

            approved = check_trade_approval(
                signal_type=side,
                current_price=entry_price,
                adx_htf=adx_htf,
                ema_htf=ema_htf,
                current_position_count=len(positions)
            )
            
            if not approved:
                await asyncio.sleep(0.4)
                continue
            
            # [Bug Fix 1] 실시간 잔고 기반 수량 계산 (wallet 인자는 참고용, 내부에서 재조회)
            qty = await calc_qty(symbol, entry_price, wallet, engine_name)
            if qty <= 0:
                await asyncio.sleep(0.4)
                continue

            tp, sl = calc_tp_sl(symbol, side, entry_price, atr, engine_name)
            print_signal(side, symbol, engine_name, MAIN_LEV, qty, entry_price, tp, sl)
            
            ok = await place_hybrid_order(
                symbol=symbol, side=side, qty=qty,
                entry_price=entry_price, tp_price=tp, sl_price=sl,
            )
            if ok:
                record_entry(symbol, engine_name)

        except Exception as e:
            if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
            log.error(f"{symbol} 오류: {e}")
        
        await asyncio.sleep(0.4)


async def main():
    setup_logging()
    print_banner()
    log.info("🚀 봇 가동 시작 (SMC Sniper V3 - Survival Fixed)")

    await refresh_watchlist()
    try:
        wallet = await get_balance()
    except Exception as e:
        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
        log.critical(f"초기 지갑 정보 획득 실패: {e}")
        return

    await run_regime_update()
    
    last_positions_symbols = set()
    while True:
        try:
            wallet = await get_balance()
            positions = await get_open_positions()
            current_symbols = {p["symbol"] for p in positions}
            
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
                    except Exception as e:
                        if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e

            last_positions_symbols = current_symbols

            wait = seconds_until_next_5m()
            next_time = (dt.datetime.now() + dt.timedelta(seconds=wait)).strftime("%H:%M:%S")
            log.info(f"대기 중 — 다음 정각: {next_time} ({wait:.0f}초 후)")

            check_interval = 10
            elapsed = 0
            while elapsed < wait:
                await asyncio.sleep(min(check_interval, wait - elapsed))
                elapsed += check_interval
                try:
                    curr_pos = await get_open_positions()
                    if curr_pos:
                        await monitor_positions(curr_pos, _entry_regimes)
                except Exception as e:
                    if "CIRCUIT_BREAKER_TRIGGERED" in str(e): raise e
                    log.error(f"모니터링 오류: {e}")

            await asyncio.sleep(CANDLE_BUFFER)
            now = dt.datetime.now()
            if now.minute % 15 == 0:
                await refresh_watchlist()
                await run_regime_update()

            await run_entry_check(wallet)

        except RuntimeError as e:
            # [Bug Fix 3] RuntimeError 처리 및 continue 들여쓰기 수정
            if "CIRCUIT_BREAKER_TRIGGERED" in str(e):
                log.critical("🚨 [CIRCUIT BREAKER] 중요 API 연속 실패! 봇을 60초간 대기 상태로 전환합니다.")
                await asyncio.sleep(60)
                continue # 정상적인 회로 차단 후 다음 루프로
            
            # [Bug Fix 6] 기타 RuntimeError 발생 시 무한 루프 방지
            log.error(f"기타 런타임 에러 발생: {e}. 30초 대기.")
            await asyncio.sleep(30)
            continue

        except (ConnectionError, TimeoutError) as e:
            log.warning(f"⚠️ 네트워크 에러: {e}. 10초 후 재시도...")
            await asyncio.sleep(10)
        except Exception as e:
            log.error(f"🔥 메인 루프 예외 발생: {e}")
            await asyncio.sleep(30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
