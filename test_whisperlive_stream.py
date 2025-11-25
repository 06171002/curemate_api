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

# --- 설정 ---
HOST_IP = "127.0.0.1"
TEST_AUDIO_FILE = "temp_audio/test.mp3"

# ✅ WhisperLiveKit은 내부 VAD가 있으므로 작은 청크로 자주 보내도 됨
CHUNK_DURATION_MS = 100  # 100ms 청크

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

    if stats["start_time"]:
        total_time = time.time() - stats["start_time"]
        print(f"📊 통계:")
        print(f"   - 총 실행 시간: {total_time:.2f}초")
        print(f"   - 수신한 세그먼트: {stats['segments_received']}개")
        print(f"   - 총 텍스트 길이: {stats['total_text_length']}자")
        if stats["segments_received"] > 0:
            avg_time = total_time / stats["segments_received"]
            print(f"   - 세그먼트당 평균 시간: {avg_time:.2f}초")

    print("="*60 + "\n")


def on_open(ws):
    print("\n" + "="*60)
    print("🚀 WebSocket 연결 성공 (WhisperLiveKit 모드)")
    print("="*60 + "\n")

    def send_audio_stream():
        try:
            # 1. 오디오 파일 로드
            print(f"📂 [1/4] 오디오 파일 로드: {TEST_AUDIO_FILE}")
            audio = AudioSegment.from_file(TEST_AUDIO_FILE)

            # 2. 16kHz, Mono, 16-bit PCM으로 변환
            print(f"🔄 [2/4] 오디오 변환 중...")
            audio = audio.set_frame_rate(16000)
            audio = audio.set_channels(1)
            audio = audio.set_sample_width(2)

            # 3. 청크 크기 계산
            frame_size_bytes = int(16000 * (CHUNK_DURATION_MS / 1000.0) * 2)
            audio_bytes = audio.raw_data
            total_chunks = len(audio_bytes) // frame_size_bytes
            audio_duration = len(audio_bytes) / (16000 * 2)

            print(f"✅ [3/4] 오디오 정보:")
            print(f"   - 총 길이: {audio_duration:.2f}초")
            print(f"   - 청크 크기: {CHUNK_DURATION_MS}ms ({frame_size_bytes} bytes)")
            print(f"   - 총 청크 수: {total_chunks}개")
            print(f"\n▶️  [4/4] 스트리밍 시작...\n")

            # 4. 청크 전송
            last_progress = -1
            for i in range(total_chunks):
                start = i * frame_size_bytes
                end = start + frame_size_bytes
                chunk = audio_bytes[start:end]

                if len(chunk) < frame_size_bytes:
                    break

                ws.send(chunk, websocket.ABNF.OPCODE_BINARY)

                # 실시간 시뮬레이션
                time.sleep(CHUNK_DURATION_MS / 1000.0)

                # 진행 상황 출력 (10%마다)
                progress = int((i + 1) / total_chunks * 10) * 10
                if progress != last_progress and progress % 10 == 0:
                    print(f"📤 전송 진행률: {progress}% ({i+1}/{total_chunks} 청크)")
                    last_progress = progress

            print(f"\n✅ [전송 완료] 모든 오디오 데이터 전송 완료")
            print(f"⏳ 서버 처리 대기 중 (최대 30초)...\n")

            # 5. 서버 처리 대기
            time.sleep(30)
            ws.close()

        except FileNotFoundError:
            print(f"\n❌ 테스트 파일({TEST_AUDIO_FILE})을 찾을 수 없습니다!")
            print(f"   1. 파일 경로를 확인하세요")
            print(f"   2. temp_audio 폴더가 존재하는지 확인하세요\n")
            ws.close()
        except Exception as e:
            print(f"\n❌ 오디오 전송 중 오류: {e}")
            import traceback
            traceback.print_exc()
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
    print(f"   - 청크 크기: {CHUNK_DURATION_MS}ms")
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