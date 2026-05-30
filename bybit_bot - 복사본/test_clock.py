import sys
sys.stdout = open("test_clock_result.txt", "w", encoding="utf-8")
sys.stderr = sys.stdout

import time
import requests

# 로컬 시간
local_ms = int(time.time() * 1000)

# Bybit 테스트넷 서버 시간
try:
    r = requests.get("https://api-testnet.bybit.com/v5/market/time", timeout=5)
    data = r.json()
    server_ms = int(data["result"]["timeSecond"]) * 1000
    diff = local_ms - server_ms
    print(f"로컬  시간(ms): {local_ms}")
    print(f"서버  시간(ms): {server_ms}")
    print(f"차이(ms):       {diff}")
    print()
    if abs(diff) > 5000:
        print(f"!! 시계 오차 {abs(diff)//1000}초 → 서명 실패 원인")
        print("   Windows 시간 동기화 필요: 날짜/시간 설정 → '지금 동기화'")
    else:
        print(f"시계 오차 OK ({abs(diff)}ms) — 시간 문제 아님")
        print()
        print("다른 원인 가능성:")
        print("  1. API 키에 IP 제한이 걸려 있는 경우")
        print("     → Bybit 테스트넷 API 관리에서 해당 키의 'IP 제한' 항목 확인")
        print("  2. 키/시크릿 불일치 (다른 키의 시크릿을 사용)")
        print("     → 테스트넷에서 새 키를 재발급받아 .env에 다시 입력")
except Exception as e:
    print(f"서버 시간 조회 오류: {e}")

sys.stdout.close()
