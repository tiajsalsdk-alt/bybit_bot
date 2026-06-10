import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Bybit API ────────────────────────────────────────────
API_KEY    = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
DEMO       = os.getenv("DEMO", "false").lower() == "true"

# ── 종목 스캔 ─────────────────────────────────────────────
VIP_SYMBOLS      = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT",
    "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "LINKUSDT", "UNIUSDT",
]
FIXED_WATCHLIST  = False       # True: VIP_SYMBOLS만 거래, False: 스캔 사용
BLACKLIST        = ["CLUSDT", "XAUTUSDT", "PAXGUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "EURUSDT"]
SCAN_EXCLUDE_TOP = 30
MIN_VOLUME       = 5_000_000

# ── 리스크 관리 ───────────────────────────────────────────
DAILY_LOSS_LIMIT = 0.10    # 하루 10% 손실 시 당일 거래 중단

# ── 포지션 크기 ───────────────────────────────────────────
TREND_SEED_PCT = 0.15      # 거래당 증거금 비율 (잔고의 15%)
MAIN_LEV       = 10        # 레버리지

# ── 동시 포지션 한도 ──────────────────────────────────────
DYNAMIC_MAX_TREND  = 6     # 추세장 최대 포지션 수
DYNAMIC_MAX_SIDE   = 3     # 횡보장 최대 포지션 수
MAX_SAME_DIR       = 3     # 같은 방향(롱/숏) 동시 포지션 상한

# ── FVG 브레이크아웃 전략 ─────────────────────────────────
DON_TIME_STOP_BARS   = 60    # 타임스탑: 60봉 × 15분 = 15시간

FVG_SL_ATR_BUFFER    = 1.5   # 구조적 손절 ATR 버퍼
FVG_RISK_CAP_ATR_MULT= 2.5   # 손절폭 최대 허용 (ATR 배수)
USE_TP1              = False  # TP1 반익절 사용 여부 (False=비활성화, 백테스트 기준 +1.5% vs -0.2%)
TREND_TP1_R_MULT     = 0.9   # 스마트TP 아밍 R배수 ★수수료 반영 최적값
TREND_TP2_R_MULT     = 1.8   # 2차 익절 (1.8R → 완전 청산) ★15분봉 최적값

# ── 장세 판단 기준 ────────────────────────────────────────
ADX_TREND_LEVEL    = 24    # BTC ADX ≥ 24 → 추세장 (최대 포지션 7개)
ADX_SIDEWAYS_LEVEL = 23    # BTC ADX < 24 → 횡보장 (최대 포지션 3개)

ADX_TREND    = 30          # 개별 심볼: ADX ≥ 30 → FVG 진입 ★백테스트 최적값
ADX_SIDEWAYS = 22          # 개별 심볼: ADX ≤ 22 → 대기

# ── 타임프레임 ────────────────────────────────────────────
TF_REGIME      = "15"      # 장세 판단용 봉 (15분)
TF_ENTRY       = "15"      # 진입 신호용 봉 (15분) ★ 백테스트 최적 TF
CANDLES_NEEDED = 300

# ── 지표 파라미터 ──────────────────────────────────────────
ADX_LEN  = 14
EMA_LEN  = 200
ATR_LEN  = 14

# ── 주문 실행 ─────────────────────────────────────────────
LIMIT_TIMEOUT = 10

# ── AWS Kill-Switch & Backoff ────────────────────────────
MAX_CONSECUTIVE_ERRORS = 5
BACKOFF_SECONDS        = [5, 10, 20, 40, 80]
