"""
WhisperLiveKit 기반 실시간 스트리밍 테스트 (개선 버전)

서버 측 WhisperLiveKit 처리 결과를 실시간으로 확인
"""

import requests
import websocket
import threading
import time
import sys
import json
from pydub import AudioSegment
import os

# --- 설정 ---
HOST_IP = "127.0.0.1"
TEST_AUDIO_FILE = "../temp_audio/test.mp3"


# 4KB 단위로 끊어서 전송 (일반적인 스트리밍 방식)
CHUNK_SIZE = 4096
# 전송 간격 (너무 빠르면 버퍼 오버플로우 가능성, 0.02~0.05초 적당)
SEND_INTERVAL = 0.05

API_BASE_URL = f"http://{HOST_IP}:8000"
WS_BASE_URL = f"ws://{HOST_IP}:8000"

# ✅ 통계 추적
stats = {
    "segments_received": 0,
    "total_text_length": 0,
    "start_time": None,
    "last_segment_time": None
}


def on_message(ws, message):
    """서버로부터 메시지 수신 시 호출"""
    try:
        data = json.loads(message)
        msg_type = data.get("type", "unknown")

        if msg_type == "connection_success":
            print(f"\n✅ [연결 성공] {data.get('message')}\n")
            stats["start_time"] = time.time()

        elif msg_type == "transcript_segment":
            # ✅ STT 결과 수신
            stats["segments_received"] += 1
            stats["last_segment_time"] = time.time()

            segment_num = data.get("segment_number", "?")
            text = data.get("text", "")
            stats["total_text_length"] += len(text)

            print(f"\n🗣️  [세그먼트 #{segment_num}]")
            print(f"   📝 텍스트: {text}")
            if "processing_time_ms" in data:
                print(f"   ⏱️  처리 시간: {data['processing_time_ms']:.2f}ms")
            print()

        elif msg_type == "final_summary":
            # ✅ 최종 요약 수신
            summary = data.get("summary", {})
            total_segments = data.get("total_segments", 0)

            print("\n" + "="*60)
            print("📊 최종 요약 수신")
            print("="*60)
            print(f"총 세그먼트: {total_segments}")
            print(f"요약 내용: {json.dumps(summary, ensure_ascii=False, indent=2)}")
            print("="*60 + "\n")

        elif msg_type == "error":
            print(f"\n❌ [서버 오류] {data.get('message')}\n")

        else:
            print(f"\n🔔 [알 수 없는 메시지] {message}\n")

    except json.JSONDecodeError:
        print(f"\n⚠️  [JSON 파싱 실패] {message}\n")
    except Exception as e:
        print(f"\n❌ [메시지 처리 오류] {e}\n")


def on_error(ws, error):
    print(f"\n🔴 [WebSocket 오류] {error}\n")


def on_close(ws, close_status_code, close_msg):
    print("\n" + "="*60)
    print("🏁 WebSocket 연결 종료")
    print("="*60)


def on_open(ws):
    print("\n" + "=" * 60)
    print("🚀 WebSocket 연결 성공 (WhisperLiveKit 모드)")
    print("=" * 60 + "\n")

    def send_audio_stream():
        try:
            print(f"📂 [1/2] 오디오 파일 열기: {TEST_AUDIO_FILE}")

            if not os.path.exists(TEST_AUDIO_FILE):
                print(f"\n❌ 테스트 파일({TEST_AUDIO_FILE})을 찾을 수 없습니다!")
                ws.close()
                return

            file_size = os.path.getsize(TEST_AUDIO_FILE)
            sent_bytes = 0

            print(f"▶️  [2/2] 스트리밍 시작... (파일 크기: {file_size / 1024:.2f} KB)\n")

            # ✅ 파일 자체를 바이너리로 읽어서 전송 (Raw PCM 변환 X)
            with open(TEST_AUDIO_FILE, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    ws.send(chunk, websocket.ABNF.OPCODE_BINARY)
                    sent_bytes += len(chunk)

                    # 전송 속도 조절 (실시간 시뮬레이션)
                    time.sleep(SEND_INTERVAL)

                    # 진행률 표시
                    progress = int((sent_bytes / file_size) * 100)
                    if progress % 10 == 0:
                        sys.stdout.write(f"\r📤 전송 중... {progress}%")
                        sys.stdout.flush()

            print(f"\n\n✅ [전송 완료] 모든 데이터 전송됨. 서버 처리 대기 중...")

            # 서버가 처리를 완료할 시간을 줌 (최대 60초)
            # final_summary를 받으면 on_message에서 close() 함
            time.sleep(60)
            ws.close()

        except Exception as e:
            print(f"\n❌ 오디오 전송 중 오류: {e}")
            ws.close()

    # 별도 스레드에서 오디오 전송 시작
    threading.Thread(target=send_audio_stream, daemon=True).start()


# --- 메인 로직 ---
def main():
    print("\n" + "="*60)
    print("WhisperLiveKit 실시간 스트리밍 테스트 (개선 버전)")
    print("="*60)
    print(f"📋 설정:")
    print(f"   - API 서버: {API_BASE_URL}")
    print(f"   - 테스트 파일: {TEST_AUDIO_FILE}")
    print(f"   - STT 엔진: WhisperLiveKit (서버 측)")
    print("="*60 + "\n")

    try:
        # 1. Job 생성
        print("🔧 [단계 1/2] Job 생성 요청...")
        response = requests.post(f"{API_BASE_URL}/api/v1/stream/create", timeout=10)
        response.raise_for_status()
        job_id = response.json().get("job_id")

        if not job_id:
            print("❌ 오류: 응답에서 job_id를 찾을 수 없습니다.")
            sys.exit(1)

        print(f"✅ Job 생성 성공! (Job ID: {job_id})\n")

        # 2. WebSocket 연결
        ws_url = f"{WS_BASE_URL}/ws/v1/stream/{job_id}"
        print(f"🔌 [단계 2/2] WebSocket 연결 시도...")
        print(f"   → {ws_url}\n")

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
        print(f"\n" + "="*60)
        print("❌ FastAPI 서버 연결 실패")
        print("="*60)
        print(f"🔍 문제 해결 방법:")
        print(f"   1. Docker 컨테이너 확인:")
        print(f"      > docker-compose ps")
        print(f"   2. 서버 로그 확인:")
        print(f"      > docker-compose logs api")
        print(f"   3. .env 파일에서 STT_ENGINE=whisperlivekit 확인")
        print(f"   4. 서버 재시작:")
        print(f"      > docker-compose restart api")
        print("="*60 + "\n")

    except requests.exceptions.Timeout:
        print(f"\n❌ 서버 응답 시간 초과 (10초)")
        print(f"   서버가 과부하 상태이거나 응답하지 않습니다.\n")

    except Exception as e:
        print(f"\n❌ 예기치 않은 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  사용자에 의해 중단되었습니다.\n")
        sys.exit(0)