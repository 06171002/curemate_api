
from typing import Dict
# (★중요) domain 패키지에서 StreamingJob을 임포트합니다.
from patient_api.domain.streaming_job import StreamingJob

# --- 1. 설정 변수 ---
TEMP_AUDIO_DIR = "temp_audio"

# --- 2. (F-JOB-02) 전역 스트림 작업 매니저 ---
# main.py에 있던 것을 여기로 이동
active_jobs: Dict[str, StreamingJob] = {}