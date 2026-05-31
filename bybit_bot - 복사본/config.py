import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Bybit API ────────────────────────────────────────────
API_KEY    = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
DEMO       = os.getenv("DEMO", "false").lower() == "true"

# ── 종목 스캔 ─────────────────────────────────────────────
VIP_SYMBOLS   = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
BLACKLIST     = ["CLUSDT", "XAUTUSDT", "PAXGUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "EURUSDT"]                                   
SCAN_EXCLUDE_TOP = 20                                        
MIN_VOLUME    = 50_000_000                                    

# ── 포지션 및 리스크 관리 ───────────────────────────────────
MAX_TOTAL_USAGE    = 0.90    
CASH_RESERVE_PCT   = 0.10    
DAILY_LOSS_LIMIT   = 0.10    

# ── [MTF Regime & Entry TFs] ──────────────────────────
ADX_TF         = "60"    # 장세 판별: 1시간봉 (1H)
SIDEWAYS_TF    = "15"    # 횡보장 타점: 15분봉
TREND_TF       = "5"     # 추세장 타점: 5분봉

# ── [FVG Trend & Sideways BB] 하이브리드 엔진 설정 ──────────────
ADX_TREND_LEVEL    = 25      # 25 이상 추세장 (FVG 전용)
ADX_SIDEWAYS_LEVEL = 22      # 22 이하 횡보장 (BB+Stoch 전용)
# [23~24]: 데드존 (자동 관망)
EMA_H1_FILTER      = 50      # 1시간봉 대추세 필터
FVG_VOL_MULT       = 1.3     # FVG 거래량 필터 (1.3배)
FEE_BUFFER         = 0.0015  # 본절 이동 수수료 버퍼 (0.15%)
ENTRY_TIMEOUT      = 5       # 지정가 대기 타임아웃 (5초)

# [자금 관리] 실시간 총 잔고 대비 장세별 진입 비중
WEIGHT_SIDEWAYS    = 0.20    # 횡보장 20% 투입
WEIGHT_TREND       = 0.15    # 추세장 15% 투입

MAIN_LEV           = 10      # 기본 레버리지 10배

# 손절/익절 설정 (꼬리 휩쏘 방어형 손익비 1:1.5)
ATR_SL_MULT_TREND    = 1.2   # 추세장 손절 (ATR 1.2배 - 휩쏘 방어)
ATR_TP_MULT_TREND    = 1.8   # 추세장 익절 (ATR 1.8배)
ATR_SL_MULT_SIDEWAYS = 2.0   # 횡보장 손절 (ATR 2.0배)
ROE_TAKE_PROFIT_PCT  = 0.07   # 횡보장 익절 기준 (7%)
TREND_HALF_TP_ROE    = 0.07   # 추세장 반익절 기준 (7%)

# Bollinger Bands & StochRSI
BB_LEN             = 20
BB_STD             = 1.5
STOCH_PRO_LOW      = 30
STOCH_PRO_HIGH     = 70
STOCH_ANTI_LOW     = 20
STOCH_ANTI_HIGH    = 80

# ── 타임프레임 및 버퍼 ─────────────────────────────────────
CANDLES_NEEDED = 300     

# ── 지표 파라미터 ──────────────────────────────────────────
ADX_LEN        = 14
EMA_LEN        = 200
EMA_PULLBACK_LEN = 20    # 5m 단기 이평선 (눌림목용)
ATR_LEN        = 14
RSI_LEN        = 14

TS_ATR_MULT    = 1.5   # 트레일링 스탑 역행 배수

VOL_SMA_LEN    = 20    
VOL_MULT_SMC   = 1.2   # SMC 스윕 거래량 배수 (5분봉 환경 최적화)
VOL_MULT_PULLBACK = 1.3 # 눌림목 돌파 거래량 배수 (1.5 -> 1.3 완화)

# 로직 2: StochRSI 상세 세팅
STOCH_RSI_LEN  = 14
STOCH_K_LEN    = 14
STOCH_D_LEN    = 3
STOCH_SMOOTH   = 3

LIMIT_TIMEOUT  = 10    
