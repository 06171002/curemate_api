# check_models.py
import os
from google import genai
from dotenv import load_dotenv

# .env 파일 로드 (API 키가 .env에 있다면)
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY") # 또는 직접 입력: "YOUR_API_KEY"

if not api_key:
    print("API 키가 설정되지 않았습니다.")
else:
    client = genai.Client(api_key=api_key)
    print("=== 사용 가능한 모델 목록 ===")
    try:
        # v1beta 기준 모델 리스트 조회
        for model in client.models.list():
            # generateContent를 지원하는 모델만 출력
            if "generateContent" in model.supported_actions:
                print(f"- {model.name} (Display Name: {model.display_name})")
    except Exception as e:
        print(f"모델 목록 조회 실패: {e}")