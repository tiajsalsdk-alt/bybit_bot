import sys
sys.stdout = open("test_api_result.txt", "w", encoding="utf-8")
sys.stderr = sys.stdout

from dotenv import load_dotenv
load_dotenv()
import os

key    = os.getenv("BYBIT_API_KEY", "")
secret = os.getenv("BYBIT_API_SECRET", "")
demo   = os.getenv("DEMO", "false").lower() == "true"

print(f"KEY 길이: {len(key)}")
print(f"SECRET 길이: {len(secret)}")
print(f"DEMO: {demo}")
print(f"연결 서버: {'api-demo.bybit.com' if demo else 'api.bybit.com'}")
print()

from pybit.unified_trading import HTTP
session = HTTP(demo=demo, api_key=key, api_secret=secret)

# 1. 잔고 조회 (인증 필요)
try:
    r = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
    print(f"잔고 조회: retCode={r['retCode']} / {r.get('retMsg')}")
    if r['retCode'] == 0:
        print("인증 성공!")
    else:
        print(f"실패 코드: {r['retCode']} → {r.get('retMsg')}")
except Exception as e:
    print(f"잔고 조회 오류: {e}")

# 2. 포지션 조회
try:
    r2 = session.get_positions(category="linear", settleCoin="USDT")
    print(f"포지션 조회: retCode={r2['retCode']} / {r2.get('retMsg')}")
except Exception as e:
    print(f"포지션 조회 오류: {e}")

sys.stdout.close()
