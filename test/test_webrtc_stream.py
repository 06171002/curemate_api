"""
WebRTC 원본 스트림 전송 시뮬레이션

실제 WebRTC가 보내는 것처럼 원본 오디오를 그대로 전송
"""

import requests
import websocket
import threading
import time
import sys
import json
import os
from pydub import AudioSegment

# --- 설정 ---
# HOST_IP = "172.30.1.4"
HOST_IP = "127.0.0.1"
current_dir = os.path.dirname(os.path.abspath(__file__))
TEST_AUDIO_FILE = os.path.join(current_dir, "..", "temp_audio", "test4.mp3")  # (★ 본인의 MP3 파일 경로로 수정!)

# WebRTC가 보낼 청크 크기 (가변적, 보통 20-60ms 분량)
# 실제 WebRTC는 네트워크 상황에 따라 다양한 크기로 보냄
CHUNK_SIZE = 4096  # 약 4KB (가변 청크 시뮬레이션)
SEND_INTERVAL = 0.02  # 20ms마다 전송 (실시간 시뮬레이션)

API_BASE_URL = f"http://{HOST_IP}:8000"
WS_BASE_URL = f"ws://{HOST_IP}:8000"

# 통계
stats = {
    "segments_received": 0,
    "total_text_length": 0,
    "start_time": None
}


def on_message(ws, message):
    """서버로부터 메시지 수신"""
    try:
        data = json.loads(message)
        msg_type = data.get("type", "unknown")

        if msg_type == "connection_success":
            print(f"\n✅ [연결 성공] {data.get('message')}")
            print(f"   VAD 설정: {data.get('vad_config')}\n")
            stats["start_time"] = time.time()

        elif msg_type == "transcript_segment":
            stats["segments_received"] += 1

            segment_num = data.get("segment_number", "?")
            text = data.get("text", "")
            processing_ms = data.get("processing_time_ms", 0)

            stats["total_text_length"] += len(text)

            print(f"\n🗣️  [세그먼트 #{segment_num}]")
            print(f"   📝 텍스트: {text}")
            print(f"   ⏱️  처리 시간: {processing_ms:.2f}ms")
            print()

        elif msg_type == "final_summary":
            summary = data.get("summary", {})
            total_segments = data.get("total_segments", 0)

            elapsed = time.time() - stats["start_time"] if stats["start_time"] else 0

            print("\n" + "="*60)
            print("📊 최종 요약")
            print("="*60)
            print(f"총 세그먼트: {total_segments}")
            print(f"총 처리 시간: {elapsed:.2f}초")
            print(f"요약 내용: {json.dumps(summary, ensure_ascii=False, indent=2)}")
            print("="*60 + "\n")

        elif msg_type == "error":
            print(f"\n❌ [서버 오류] {data.get('message')}\n")

        else:
            print(f"\n🔔 [알 수 없는 메시지] {message}\n")

    except json.JSONDecodeError:
        print(f"\n⚠️  [JSON 파싱 실패] {message}\n")


def on_error(ws, error):
    print(f"\n🔴 [WebSocket 오류] {error}\n")


def on_close(ws, close_status_code, close_msg):
    print("\n" + "="*60)
    print("🏁 WebSocket 연결 종료")
    print("="*60)
    print(f"수신한 세그먼트: {stats['segments_received']}개")
    print(f"총 텍스트 길이: {stats['total_text_length']}자")
    print("="*60 + "\n")


def on_open(ws):
    print("\n" + "="*60)
    print("🚀 WebSocket 연결 성공 (WebRTC 시뮬레이션 모드)")
    print("="*60 + "\n")

    def send_webrtc_stream():
        """
        실제 WebRTC처럼 원본 오디오를 가변 크기로 전송
        (서버 측에서 변환 처리)
        """
        try:
            print(f"📂 [1/2] 오디오 파일 열기: {TEST_AUDIO_FILE}")

            if not os.path.exists(TEST_AUDIO_FILE):
                print(f"\n❌ 테스트 파일({TEST_AUDIO_FILE})을 찾을 수 없습니다!")
                ws.close()
                return

            # =========================================================
            # [수정 포인트] 서버의 PyAV 설정(48k, Stereo)에 맞춤
            # =========================================================
            target_sample_rate = 48000
            target_channels = 2

            audio = AudioSegment.from_file(TEST_AUDIO_FILE)
            audio = audio.set_frame_rate(target_sample_rate)
            audio = audio.set_channels(target_channels)
            audio = audio.set_sample_width(2)  # 16-bit

            raw_data = audio.raw_data

            # 30ms 청크 크기 계산
            # 48000 * 0.03(30ms) * 2(Stereo) * 2(16bit bytes) = 5760 bytes
            chunk_size = int(target_sample_rate * 0.03 * target_channels * 2)

            print(f"ℹ️  오디오 변환: {target_sample_rate}Hz, {target_channels}ch")
            print(f"ℹ️  청크 크기: {chunk_size} bytes (30ms)")

            offset = 0
            while offset < len(raw_data):
                chunk = raw_data[offset:offset + chunk_size]
                ws.send(chunk, websocket.ABNF.OPCODE_BINARY)

                offset += chunk_size
                time.sleep(0.03)  # 30ms 대기 (실시간성 모사)

            print("   서버 처리 대기 중...\n")

            print("   (서버 로그에서 진행 상황을 확인하세요)\n")

            time.sleep(60)  # 3분 대기
            ws.close()

        except Exception as e:
            print(f"\n❌ 오디오 전송 중 오류: {e}")
            ws.close()

    # 별도 스레드에서 전송 시작
    threading.Thread(target=send_webrtc_stream, daemon=True).start()


# --- 메인 로직 ---
def main():

    try:
        # 1. Job 생성 (오디오 포맷 명시)
        print("🔧 [단계 1/2] Job 생성 요청...")

        payload = {
            "audio_format": "pcm",  # 필수
            "sample_rate": 48000,  # 선택 (PCM이라 권장)
            "channels": 2,  # 선택 (PCM이라 권장)

            # 👇 확인하고 싶은 값 추가
            "cure_seq": 101,  # 테스트할 치료 ID
            "cust_seq": 5004,  # 테스트할 환자 ID

            # (필요시) 화상회의 정보
            "room_id": "test_room",
            "member_id": "tester"
        }

        response = requests.post(
            f"{API_BASE_URL}/api/v1/stream/create",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        job_id = result.get("job_id")

        if not job_id:
            print("❌ 오류: 응답에서 job_id를 찾을 수 없습니다.")
            sys.exit(1)

        print(f"✅ Job 생성 성공! (Job ID: {job_id})")

        # ✅ 경고 메시지 표시
        if result.get("warning"):
            print(f"\n⚠️  {result['warning']}\n")
        else:
            print()

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

        # ✅ Ping 간격을 길게 설정 (MP3 전체 변환 + STT 대기)
        ws.run_forever(
            ping_interval=90,  # 90초마다 ping
            ping_timeout=60  # 60초 타임아웃
        )

    except requests.exceptions.ConnectionError:
        print(f"\n" + "="*60)
        print("❌ FastAPI 서버 연결 실패")
        print("="*60)
        print(f"🔍 해결 방법:")
        print(f"   1. Docker 컨테이너 확인:")
        print(f"      > docker-compose ps")
        print(f"   2. 서버 로그 확인:")
        print(f"      > docker-compose logs api")
        print(f"   3. 서버 재시작:")
        print(f"      > docker-compose restart api")
        print("="*60 + "\n")

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