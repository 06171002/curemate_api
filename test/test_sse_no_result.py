import os
import requests
import json
import sseclient  # pip install sseclient-py
from pydub import AudioSegment  # pip install pydub

# --- 설정 ---
HOST = "127.0.0.1:8000"
API_URL = f"http://{HOST}/api/v1/conversation/request"
STREAM_URL_TEMPLATE = f"http://{HOST}/api/v1/conversation/stream-events/{{job_id}}"
SILENT_FILE_PATH = "silent_test.mp3"


def create_silent_mp3(duration_ms=3000):
    """3초짜리 완전 무음 MP3 파일 생성"""
    print(f"🔇 [준비] {duration_ms}ms 무음 파일 생성 중: {SILENT_FILE_PATH}")
    silent_audio = AudioSegment.silent(duration=duration_ms)
    silent_audio.export(SILENT_FILE_PATH, format="mp3")


def run_test():
    # 1. 무음 파일 생성
    create_silent_mp3()

    try:
        # 2. 파일 업로드 (Job 생성)
        print("🚀 [1단계] 무음 파일 업로드 및 작업 생성 요청...")
        with open(SILENT_FILE_PATH, 'rb') as f:
            files = {'file': f}
            # 필수 파라미터가 있다면 data에 추가 (예: cure_seq 등)
            response = requests.post(API_URL, files=files)
            response.raise_for_status()

        result = response.json()
        job_id = result.get("job_id")
        print(f"✅ Job 생성 성공! Job ID: {job_id}")

        # 3. SSE 연결 및 이벤트 수신
        stream_url = STREAM_URL_TEMPLATE.format(job_id=job_id)
        print(f"\n📡 [2단계] SSE 스트림 연결 시도: {stream_url}")

        # stream=True로 요청
        response = requests.get(stream_url, stream=True)
        client = sseclient.SSEClient(response)

        print("👂 이벤트 수신 대기 중...\n")

        for event in client.events():
            print(f"📨 [이벤트 수신] Type: {event.event}")
            print(f"   Data: {event.data}")

            # 데이터 파싱
            try:
                data = json.loads(event.data)
            except:
                data = {}

            # 종료 조건 확인
            if event.event == 'error':
                print("\n✅ [테스트 성공] 'error' 이벤트 수신함.")
                print(f"   메시지: {data.get('message')}")
                break

            if event.event == 'final_summary':
                print("\n⚠️ [예상 밖] 요약이 완료되었습니다 (무음이 아닐 수 있음).")
                break

        print("\n🏁 [3단계] SSE 연결 종료 확인 (루프 탈출)")

    except Exception as e:
        print(f"\n❌ 테스트 실패: {e}")
    finally:
        # 파일 정리
        if os.path.exists(SILENT_FILE_PATH):
            os.remove(SILENT_FILE_PATH)
            print("🧹 임시 파일 삭제 완료")


if __name__ == "__main__":
    # 필요 라이브러리 체크
    try:
        import sseclient
        import pydub
    except ImportError:
        print("필요한 라이브러리를 먼저 설치해주세요:")
        print("pip install sseclient-py pydub")
        exit(1)

    run_test()