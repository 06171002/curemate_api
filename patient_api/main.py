import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from typing import Dict

# --- 1. 우리가 만든 서비스 모듈 임포트 ---
# (Lifespan에서 사용하기 위해 필요)
from patient_api.services import ollama_service, stt_service 
# (라우터 임포트)
from patient_api.api import batch_endpoints, stream_endpoints
# (도메인 클래스 임포트)
from patient_api.domain.streaming_job import StreamingJob

# --- 2. 설정 ---
# (라우터 파일들이 이 변수를 임포트해갈 수 있음)
TEMP_AUDIO_DIR = "temp_audio"

# (F-JOB-02) StreamJobManager: 활성 스트림 작업을 관리하는 전역 딕셔너리
# (api/stream_endpoints.py 파일에서 이 변수를 임포트하여 사용)
active_jobs: Dict[str, StreamingJob] = {}


# --- 3. Lifespan 이벤트 핸들러 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    서버 시작/종료 시 실행되는 이벤트 핸들러
    """
    print("INFO:     서버가 시작됩니다.")

    # 임시 오디오 디렉터리 생성
    os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
    print(f"INFO:     임시 오디오 디렉터리 확인: {TEMP_AUDIO_DIR}")

    # 1. STT 모델 로드
    stt_service.load_stt_model()

    # 2. Ollama 서버 연결 확인
    await ollama_service.check_ollama_connection()

    yield
    # --- 서버 종료 시 실행될 코드 ---
    print("INFO:     서버가 종료됩니다.")


# --- 4. FastAPI 앱 생성 ---
app = FastAPI(
    title="CureMate STT/Summary API",
    description="음성 대화 STT 및 요약 비동기 API 명세서",
    version="1.0.0",
    lifespan=lifespan  # Lifespan 함수 연결
)


# --- 5. (★핵심) API 라우터 포함 ---

@app.get("/")
def read_root():
    return {"message": "CureMate API (v1) is running!"}

# "파일 후처리" API 라우터 (Celery/Polling)
app.include_router(batch_endpoints.router, tags=["Batch Conversation (File)"])

# "실시간 스트리밍" API 라우터 (WebSocket)
app.include_router(stream_endpoints.router, tags=["Real-time Stream (WebSocket)"])