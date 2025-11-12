# test_websocket.py

import requests
import websocket  # (pip install websocket-client)
import threading
import time
import sys

# ================================================================
# (★매우 중요★) 이 IP를 본인의 WSL 2 IP로 수정하세요!
#
# (WSL 터미널에서)
# $ ip addr show eth0 | grep "inet "
# (예: "inet 172.20.192.50/20 ...")
#
# (또는 Windows PowerShell에서)
# > ipconfig
# (vEthernet (WSL) 어댑터의 IPv4 주소)
# WSL_HOST_IP = "172.21.192.1"
WSL_HOST_IP = "localhost"
# ================================================================

API_BASE_URL = f"http://{WSL_HOST_IP}:8000"
WS_BASE_URL = f"ws://{WSL_HOST_IP}:8000"


# --- WebSocket 이벤트 핸들러 ---
def on_message(ws, message):
    print(f"\n[WebSocket] ⬅️ 서버로부터 메시지 수신:\n{message}\n")


def on_error(ws, error):
    print(f"\n[WebSocket] 🔴 오류 발생: {error}\n")


def on_close(ws, close_status_code, close_msg):
    print("\n[WebSocket] ### 연결 종료됨 ###\n")


def on_open(ws):
    print("[WebSocket] ### 연결 성공 (on_open) ###")

    def send_audio_chunks():
        # (F-JOB-01/F-VAD-01) StreamingJob/VADProcessor가
        # 30ms (960 bytes @ 16kHz/16-bit) 청크를 기대합니다.
        fake_audio_chunk = b'\x00' * 960

        # 5초간 0.5초마다 가짜 오디오 데이터 전송 (총 10회)
        try:
            for i in range(10):
                time.sleep(0.5)
                ws.send(fake_audio_chunk, websocket.ABNF.OPCODE_BINARY)
                print(f"[WebSocket] ➡️ 960 바이트 오디오 청크 전송 ({i + 1}/10)")

            # 5초 뒤 연결 종료
            time.sleep(1)
            ws.close()
            print("[WebSocket] ➡️ 테스트 완료, 연결 종료 요청.")
        except Exception as e:
            print(f"[WebSocket] ➡️ 전송 중 오류 (서버가 먼저 닫혔을 수 있음): {e}")

    # 별도 스레드에서 데이터 전송 시작
    threading.Thread(target=send_audio_chunks).start()


# --- 1. POST 요청으로 Job 생성 (F-API-03) ---
print(f"--- 1. {API_BASE_URL}/api/v1/stream/create 에 Job 생성 요청 ---")
try:
    #
    response = requests.post(f"{API_BASE_URL}/api/v1/stream/create")
    response.raise_for_status()
    job_data = response.json()
    job_id = job_data.get("job_id")

    if not job_id:
        print("🔴 오류: 응답에서 job_id를 찾을 수 없습니다.")
        sys.exit()

    print(f"🟢 Job 생성 성공! (Job ID: {job_id})")

    # --- 2. WebSocket 연결 (F-API-04) ---
    ws_url = f"{WS_BASE_URL}/ws/v1/stream/{job_id}"
    print(f"\n--- 2. {ws_url} 에 WebSocket 연결 시도 ---")

    ws = websocket.WebSocketApp(ws_url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)

    ws.run_forever()

except requests.exceptions.ConnectionError as e:
    print(f"🔴🔴🔴 FastAPI 서버({API_BASE_URL}) 연결 실패 🔴🔴🔴")
    print(f"1. Uvicorn이 {API_BASE_URL}에서 실행 중인지 확인하세요.")
    print(f"2. (WSL 사용 시) IP 주소({WSL_HOST_IP})가 올바른지 확인하세요.")
    print(f"3. Windows 방화벽에서 8000번 포트가 열려있는지 확인하세요.")
except Exception as e:
    print(f"🔴 예기치 않은 오류: {e}")