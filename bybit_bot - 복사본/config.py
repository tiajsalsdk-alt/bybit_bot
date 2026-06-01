import os
from pathlib import Path

# ── [V3.5 Master Full-Fill Engine Configuration] ──

# API 인증 정보 (보안을 위해 비움)
API_KEY    = ""
API_SECRET = ""
DEMO       = False  # 실전 매매 모드

# ── 종목 필터링 ──────────────────────────────────────────
VIP_SYMBOLS   = ["ETHUSDT", "SOLUSDT"]
BLACKLIST     = ["CLUSDT", "XAUTUSDT", "PAXGUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "EURUSDT"]
MIN_VOLUME    = 50_000_000
SCAN_EXCLUDE_TOP = 20

# ── 장세 및 추세 필터 ──────────────────
ADX_TF             = "60"    # 1시간봉 기준 장세 판별
ADX_TREND_LEVEL    = 25      # 25 이상 추세 (FVG)
ADX_SIDEWAYS_LEVEL = 22      # 22 이하 횡보 (23, 24 데드존은 FVG 차단/횡보 허용)
EMA_H1_FILTER      = 50      # 1시간봉 대추세 필터 (EMA 50)
FVG_VOL_MULT       = 1.3     # FVG 거래량 필터 (1.3배)
FEE_BUFFER         = 0.0015  # 본절 이동 수수료 마진 (0.15%)
LIMIT_TIMEOUT_MINS = 25      # 미체결 지정가 자동 취소

# ── 자금 관리 (10배 레버리지 / 15% 비중) ──────────────────
MAIN_LEV           = 10      # 레버리지 10배
WEIGHT_TREND       = 0.15    # 추세장 15% 투입
WEIGHT_SIDEWAYS    = 0.15    # 횡보장 15% 투입

# ── [손익비 & 휩쏘 방어] ─────────────────────────────────
ATR_SL_MULT_TREND    = 1.2   # 추세장 기본 손절 (FVG 외 사용 시)

# ── [추세장: 5m FVG Full-Fill & R-Multiple] ──────────────────
STOCH_RSI_K_LIMIT_LONG  = 80      # 롱 과열 필터
STOCH_RSI_K_LIMIT_SHORT = 20      # 숏 과매도 필터
FVG_SL_ATR_BUFFER       = 0.5     # 구조적 손절 ATR 버퍼
FVG_RISK_CAP_ATR_MULT   = 2.5     # 리스크 캡 (손절폭 제한)
TREND_TP1_R_MULT        = 1.2     # 1차 목표가 (1.2R)
TREND_TP2_R_MULT     = 2.0     # 2차 목표가 (2.0R)

# ── [횡보장: 15m 볼린저 밴드 Mean Reversion] ──────────────────
SIDEWAYS_TF        = "15"
BB_LEN             = 20
BB_STD             = 1.5
STOCH_PRO_LOW      = 30      # 과매도 기준 (30 이하 롱)
STOCH_PRO_HIGH     = 70      # 과매수 기준 (70 이상 숏)
ATR_SL_MULT_SIDEWAYS = 1.0   # 횡보장 손절 (1.0 ATR)

# ── [리스크 통제: 글로벌 진입 락] ──────────────────
COOLDOWN_FILE      = "cooldown.json" # 물리적 락 기록 파일
SL_COOLDOWN_MINS   = 30              # 손절 발생 시 진입 금지 시간 (30분)

# ── 기타 설정 ──────────────────────────────────────────
TREND_TF       = "5"     
CANDLES_NEEDED = 300     
DAILY_LOSS_LIMIT   = 0.10    
MAX_TOTAL_USAGE    = 0.90    

# 지표 계산 파라미터
ADX_LEN        = 14
EMA_LEN        = 200
ATR_LEN        = 14
RSI_LEN        = 14
VOL_SMA_LEN    = 20    
STOCH_RSI_LEN  = 14
STOCH_K_LEN    = 14
STOCH_D_LEN    = 3
STOCH_SMOOTH   = 3
