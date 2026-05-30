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
MIN_VOLUME    = 5_000_000                                    

# ── 포지션 및 리스크 관리 ───────────────────────────────────
MAX_TOTAL_USAGE    = 0.90    
CASH_RESERVE_PCT   = 0.10    
DAILY_LOSS_LIMIT   = 0.10    

# ── [Smart Money Sniper & Sideways BB] 통합 설정 ───────────
TREND_SEED_PCT     = 0.15    # 로직 1: 추세장 15% 투입
SIDEWAYS_PRO_PCT   = 0.20    # 로직 2 순방향: 20% 투입
SIDEWAYS_ANTI_PCT  = 0.10    # 로직 2 역방향: 10% 투입

MAIN_LEV = 10                
ALT_LEV  = 10   

# 손절/익절 배수 분리
ATR_SL_MULT_TREND    = 2.0   # 로직 1: 추세장 손절 (ATR 2.0배)
ATR_TP_MULT_TREND    = 4.0   # 로직 1: 추세장 익절 (ATR 4.0배)
ATR_SL_MULT_SIDEWAYS = 2.0   # [수정] 로직 2: 횡보장 손절 (ATR 2.0배)

ROE_TAKE_PROFIT_PCT  = 0.07   # 반익절 트리거 (7%)
TREND_HALF_TP_ROE    = 0.07   # 로직 1 전용 반익절 기준

# StochRSI 비대칭 임계값
STOCH_PRO_LOW    = 30        # 순방향 과매도
STOCH_PRO_HIGH   = 70        # 순방향 과매수
STOCH_ANTI_LOW   = 20        # [수정] 역방향 과매도
STOCH_ANTI_HIGH  = 80        # [수정] 역방향 과매수

ADX_TREND_LEVEL    = 24      
ADX_SIDEWAYS_LEVEL = 23      
DYNAMIC_MAX_TREND  = 5       
DYNAMIC_MAX_SIDE   = 3       

# ── 타임프레임 ────────────────────────────────────────────
TF_REGIME      = "15"    
TF_ENTRY       = "5"    
CANDLES_NEEDED = 300     

# ── 지표 파라미터 ──────────────────────────────────────────
ADX_LEN        = 14
EMA_LEN        = 200
EMA_PULLBACK_LEN = 20    # 5m 단기 이평선 (눌림목용)
ATR_LEN        = 14
RSI_LEN        = 14
BB_LEN         = 20    
BB_STD         = 1.5   # [완화] 횡보장 밴드 이탈 빈도 상향 (2.0 -> 1.5)

TS_ATR_MULT    = 1.5   # 트레일링 스탑 역행 배수

VOL_SMA_LEN    = 20    
VOL_MULT_SMC   = 1.2   # SMC 스윕 거래량 배수 (5분봉 환경 최적화)
VOL_MULT_PULLBACK = 1.3 # 눌림목 돌파 거래량 배수 (1.5 -> 1.3 완화)

# 로직 2: StochRSI 상세 세팅
STOCH_RSI_LEN  = 14
STOCH_K_LEN    = 14
STOCH_D_LEN    = 3
STOCH_SMOOTH   = 3

ADX_TREND      = 25
ADX_SIDEWAYS   = 22

# ── 주문 실행 ─────────────────────────────────────────────
LIMIT_TIMEOUT  = 10    
