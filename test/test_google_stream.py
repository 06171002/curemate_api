# test/test_google_stream.py

import requests

HOST = "127.0.0.1:8000"

payload = {
    "audio_format": "pcm",
    "sample_rate": 16000,
    "channels": 1,
    "mode": "google",  # ✅ Google 모드
    "cure_seq": 101,
    "cust_seq": 5004
}

response = requests.post(
    f"http://{HOST}/api/v1/stream/create",
    json=payload
)

print(response.json())