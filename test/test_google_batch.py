# test/test_google_batch.py

import requests

HOST = "127.0.0.1:8000"

# Google 모드로 테스트
files = {'file': open('test.mp3', 'rb')}
data = {
    'cure_seq': 101,
    'cust_seq': 5004,
    'mode': 'google'  # ✅ Google 모드
}

response = requests.post(
    f"http://{HOST}/api/v1/conversation/request",
    files=files,
    data=data
)

print(response.json())