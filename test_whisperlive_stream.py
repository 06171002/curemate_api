"""
WhisperLiveKit 기반 실시간 스트리밍 테스트

기존 test_real_audio_stream.py와 달리,
VADProcessor를 거치지 않고 WhisperLiveKit에 직접 오디오를 전달합니다.
"""

import requests
import websocket
import threading
import time
import sys
from pydub import AudioSegment

# --- 설정 ---
HOST_IP = "127.0.0.1"
TEST_AUDIO_FILE = "temp_audio/test.mp3"

# ✅ WhisperLiveKit은 내부 VAD가 있으므로 작은 청크로 자주 보내도 됨
CHUNK_DURATION_MS = 100  # 100ms 청크 (기존 30ms보다 큼)

API_BASE_URL = f"http://{HOST_IP}:8000"
WS_BASE_URL = f"ws://{HOST_IP}:8000"


def on_message(ws, message):
    print(f"\n[WebSocket] ⬅️  서버 수신:\n{message}\n")


def on_error(ws, error):
    print(f"\n[WebSocket] 🔴 오류: {error}\n")


def on_close(ws, close_status_code, close_msg):
    print("\n[WebSocket] ### 연결 종료됨 ###\n")


def on_open(ws):
    print("[WebSocket] ### 연결 성공 (WhisperLiveKit 모드) ###")

    def send_audio_stream():
        try:
            # 1. 오디오 파일 로드
            print(f"[Streamer] 1. 오디오 파일 로드: {TEST_AUDIO_FILE}")
            audio = AudioSegment.from_file(TEST_AUDIO_FILE)

            # 2. 16kHz, Mono, 16-bit PCM으로 변환
            print("[Streamer] 2. 16kHz, Mono, 16-bit PCM으로 변환 중...")
            audio = audio.set_frame_rate(16000)
            audio = audio.set_channels(1)
            audio = audio.set_sample_width(2)

            # 3. 청크 크기 계산 (100ms)
            frame_size_bytes = int(16000 * (CHUNK_DURATION_MS / 1000.0) * 2)
            print(f"[Streamer] 3. {CHUNK_DURATION_MS}ms 청크 크기: {frame_size_bytes} 바이트")

            audio_bytes = audio.raw_data
            total_chunks = len(audio_bytes) // frame_size_bytes

            print(f"[Streamer] 4. 총 {total_chunks}개의 청크 전송 시작...")
            print(f"[Streamer]    (예상 소요 시간: {total_chunks * CHUNK_DURATION_MS / 1000:.1f}초)")

            # 4. 청크 전송
            for i in range(total_chunks):
                start = i * frame_size_bytes
                end = start + frame_size_bytes
                chunk = audio_bytes[start:end]

                if len(chunk) < frame_size_bytes:
                    break

                ws.send(chunk, websocket.ABNF.OPCODE_BINARY)

                # 실시간 시뮬레이션 (100ms 간격)
                time.sleep(CHUNK_DURATION_MS / 1000.0)

                # 진행 상황 출력 (10%마다)
                if (i + 1) % (total_chunks // 10 or 1) == 0:
                    progress = (i + 1) / total_chunks * 100
                    print(f"[Streamer] 진행률: {progress:.0f}% ({i+1}/{total_chunks})")

            print(f"[Streamer] 5. 오디오 전송 완료. 30초 후 연결 종료.")
            time.sleep(30)
            ws.close()

        except FileNotFoundError:
            print(f"🔴🔴🔴 테스트 파일({TEST_AUDIO_FILE})을 찾을 수 없습니다! 🔴🔴🔴")
            ws.close()
        except Exception as e:
            print(f"[Streamer] ➡️ 오디오 전송 중 오류: {e}")
            ws.close()

    threading.Thread(target=send_audio_stream, daemon=True).start()


# --- 메인 로직 ---
print("=" * 60)
print("WhisperLiveKit 실시간 스트리밍 테스트")
print("=" * 60)
print(f"✅ 설정:")
print(f"   - API 서버: {API_BASE_URL}")
print(f"   - 테스트 파일: {TEST_AUDIO_FILE}")
print(f"   - 청크 크기: {CHUNK_DURATION_MS}ms")
print(f"   - STT 엔진: WhisperLiveKit (내장 VAD)")
print("=" * 60)

try:
    # 1. Job 생성
    print(f"\n[단계 1] Job 생성 요청...")
    response = requests.post(f"{API_BASE_URL}/api/v1/stream/create")
    response.raise_for_status()
    job_id = response.json().get("job_id")

    if not job_id:
        print("🔴 오류: 응답에서 job_id를 찾을 수 없습니다.")
        sys.exit()

    print(f"🟢 Job 생성 성공! (Job ID: {job_id})")

    # 2. WebSocket 연결
    ws_url = f"{WS_BASE_URL}/ws/v1/stream/{job_id}"
    print(f"\n[단계 2] WebSocket 연결 시도...")
    print(f"   → {ws_url}")

    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    # Ping 설정 (연결 유지)
    ws.run_forever(
        ping_interval=30,
        ping_timeout=10
    )

except requests.exceptions.ConnectionError:
    print(f"\n🔴🔴🔴 FastAPI 서버({API_BASE_URL}) 연결 실패 🔴🔴🔴")
    print(f"1. Docker 컨테이너가 실행 중인지 확인하세요:")
    print(f"   > docker-compose ps")
    print(f"2. STT_ENGINE=whisperlivekit로 설정했는지 확인하세요:")
    print(f"   > .env 파일 또는 환경 변수")
except Exception as e:
    print(f"\n🔴 예기치 않은 오류: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("테스트 종료")
print("=" * 60)