import requests
import websocket
import threading
import time
import sys
from pydub import AudioSegment

# --- (★ 1. 설정) ---
# (Uvicorn을 실행 중인 IP: 127.0.0.1 또는 Docker/WSL IP)
HOST_IP = "127.0.0.1"
# (VAD가 요구하는 프레임)
FRAME_DURATION_MS = 30
# (테스트할 오디오 파일 경로)
TEST_AUDIO_FILE = "../temp_audio/test4.mp3"  # (★ 본인의 MP3 파일 경로로 수정!)
# --------------------

API_BASE_URL = f"http://{HOST_IP}:8000"
WS_BASE_URL = f"ws://{HOST_IP}:8000"


def on_message(ws, message):
    print(f"\n[WebSocket] ⬅️  서버 수신:\n{message}\n")


def on_error(ws, error):
    print(f"\n[WebSocket] 🔴 오류: {error}\n")


def on_close(ws, close_status_code, close_msg):
    print("\n[WebSocket] ### 연결 종료됨 ###\n")


def on_open(ws):
    print("[WebSocket] ### 연결 성공 (on_open) ###")

    def send_audio_stream():
        try:
            # 1. 오디오 파일 로드
            print(f"[Streamer] 1. 오디오 파일 로드: {TEST_AUDIO_FILE}")
            audio = AudioSegment.from_file(TEST_AUDIO_FILE)

            # 2. VAD 요구사항(16kHz, 16-bit, Mono)으로 변환
            print("[Streamer] 2. 16kHz, Mono, 16-bit PCM으로 변환 중...")
            audio = audio.set_frame_rate(16000)
            audio = audio.set_channels(1)
            audio = audio.set_sample_width(2)  # (2 bytes = 16-bit)

            # 3. 30ms 청크 크기 계산
            frame_size_bytes = int(16000 * (FRAME_DURATION_MS / 1000.0) * 2)
            print(f"[Streamer] 3. 30ms 청크 크기: {frame_size_bytes} 바이트")

            audio_bytes = audio.raw_data
            total_chunks = len(audio_bytes) // frame_size_bytes

            print(f"[Streamer] 4. 총 {total_chunks}개의 청크 전송 시작 (30ms 간격)...")

            for i in range(total_chunks):
                start = i * frame_size_bytes
                end = start + frame_size_bytes
                chunk = audio_bytes[start:end]

                if len(chunk) < frame_size_bytes:
                    break  # 마지막 조각이 작으면 무시

                ws.send(chunk, websocket.ABNF.OPCODE_BINARY)

                # (중요) 실제 30ms 간격으로 전송 (실시간 시뮬레이션)
                time.sleep(0.03)

            print(f"[Streamer] 5. 오디오 전송 완료. 30초 후 연결 종료.")
            time.sleep(10)
            ws.close()

        except FileNotFoundError:
            print(f"🔴🔴🔴 테스트 파일({TEST_AUDIO_FILE})을 찾을 수 없습니다! 🔴🔴🔴")
            ws.close()
        except Exception as e:
            print(f"[Streamer] ➡️ 오디오 전송 중 오류: {e}")
            ws.close()

    threading.Thread(target=send_audio_stream, daemon=True).start()


# --- 1. POST 요청으로 Job 생성 (F-API-03) ---
print(f"--- 1. {API_BASE_URL}/api/v1/stream/create 에 Job 생성 요청 ---")
try:
    response = requests.post(f"{API_BASE_URL}/api/v1/stream/create")
    response.raise_for_status()
    job_id = response.json().get("job_id")

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
    ws.run_forever(ping_interval=0, ping_timeout=None)

except requests.exceptions.ConnectionError:
    print(f"🔴🔴🔴 FastAPI 서버({API_BASE_URL}) 연결 실패 🔴🔴🔴")
except Exception as e:
    print(f"🔴 예기치 않은 오류: {e}")