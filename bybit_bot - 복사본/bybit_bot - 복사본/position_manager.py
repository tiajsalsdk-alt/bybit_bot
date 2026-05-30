import logging
from market_data import get_session, api_call
from regime import Regime
from order_manager import close_position_market

log = logging.getLogger(__name__)

# 진입 시 어느 장세에서 들어갔는지 로컬 추적
_entry_regimes: dict[str, str] = {}


def record_entry(symbol: str, regime_str: str):
    _entry_regimes[symbol] = regime_str


async def get_open_positions() -> list[dict]:
    try:
        session = get_session()
        resp = await api_call(session.get_positions, category="linear", settleCoin="USDT")
        if not resp or resp.get("retCode") != 0:
            msg = resp.get("retMsg") if resp else "No Response"
            log.error(f"포지션 조회 에러: {msg}")
            return []
        
        # size가 0보다 큰 것만 필터링
        positions = [p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0]
        return positions
    except Exception as e:
        log.error(f"포지션 조회 중 예외: {e}")
        return []


async def check_regime_conflict(current_regimes: dict[str, Regime]):
    """
    장세 변화에 따른 강제 청산 루틴 (필요 시 활성화).
    현재는 로깅 위주로 수행.
    """
    positions = await get_open_positions()
    for pos in positions:
        sym = pos["symbol"]
        if sym not in current_regimes:
            continue
        new_regime    = current_regimes[sym]
        entry_regime  = _entry_regimes.get(sym, "UNKNOWN")

        # 장세 격리 원칙에 따른 충돌 감시 (횡보장 -> 추세장 등)
        if "SIDEWAYS" in entry_regime and new_regime not in [Regime.SIDEWAYS_UP, Regime.SIDEWAYS_DOWN]:
             log.warning(f"[REGIME CONFLICT] {sym}: 횡보 진입 → {new_regime.value} 전환.")
