# test/test_google_batch.py

import os
import requests
import sys

HOST = "127.0.0.1:8000"

# 프로젝트 일관성을 위해 temp_audio 폴더 사용
current_dir = os.path.dirname(os.path.abspath(__file__))
TEST_AUDIO_FILE = os.path.join(current_dir, "..", "temp_audio", "test.mp3")

# 파일 존재 확인
if not os.path.exists(TEST_AUDIO_FILE):
    print(f"❌ 오류: 테스트 파일({TEST_AUDIO_FILE})을 찾을 수 없습니다!")
    print(f"💡 해결 방법:")
    print(f"   1. temp_audio/ 폴더에 test.mp3 파일을 배치하세요")
    print(f"   2. 또는 파일명을 test4.mp3로 변경하세요")
    sys.exit(1)

print(f"📂 테스트 파일: {TEST_AUDIO_FILE}")

# Google 모드로 테스트
try:
    with open(TEST_AUDIO_FILE, 'rb') as audio_file:
        files = {'file': audio_file}
        data = {
            'cure_seq': 101,
            'cust_seq': 5004,
            'mode': 'google'  # ✅ Google 모드
        }

        print(f"🚀 Google Batch STT 테스트 시작...")
        response = requests.post(
            f"http://{HOST}/api/v1/conversation/request",
            files=files,
            data=data,
            timeout=30
        )

        response.raise_for_status()
        result = response.json()

        print(f"\n✅ 작업 생성 성공!")
        print(f"📋 Job ID: {result.get('job_id')}")
        print(f"📊 상태: {result.get('status')}")
        print(f"\n💡 결과 확인:")
        print(f"   http://{HOST}/api/v1/conversation/result/{result.get('job_id')}")

except FileNotFoundError:
    print(f"❌ 파일을 찾을 수 없습니다: {TEST_AUDIO_FILE}")
    sys.exit(1)
except requests.exceptions.ConnectionError:
    print(f"❌ 서버 연결 실패: {HOST}")
    print(f"💡 서버가 실행 중인지 확인하세요 (docker-compose up)")
    sys.exit(1)
except Exception as e:
    print(f"❌ 예기치 않은 오류: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)