import os
import uuid
from contextlib import asynccontextmanager
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException
)

# --- 1. 우리가 만든 서비스 모듈 임포트 ---
import stt_service
import ollama_service
import job_manager
import worker

# --- 2. 설정 ---
# 업로드된 오디오 파일을 임시 저장할 디렉터리
TEMP_AUDIO_DIR = "temp_audio"


# --- 3. (기존) Lifespan 이벤트 핸들러 ---
# 서버 시작 시 모델/서비스를 초기화합니다.
@asynccontextmanager
async def lifespan(app: FastAPI):
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
    lifespan=lifespan  # (3)번의 lifespan 함수를 연결
)


# --- 5. API 엔드포인트 구현 ---

@app.get("/")
def read_root():
    return {"message": "CureMate API (v1) is running!"}


# (F-API-01) 대화 내용 처리 요청 (비동기 작업 생성)
@app.post("/api/v1/conversation/request", status_code=202)
async def create_conversation_request(
        file: UploadFile = File(...)
):
    """
    음성 파일(mp3, wav, m4a 등)을 업로드하여 
    STT 및 요약 작업을 **백그라운드에서 시작**시킵니다.

    즉시 `job_id`를 반환합니다.
    """

    # 1. 고유한 Job ID 생성
    job_id = str(uuid.uuid4())

    # 2. 업로드된 파일을 임시 저장
    try:
        # (보안 참고: 실제 운영 시 파일 확장자/MIME 타입 검증 필수)
        file_ext = file.filename.split(".")[-1]
        temp_file_path = os.path.join(TEMP_AUDIO_DIR, f"{job_id}.{file_ext}")

        # 파일을 비동기로 읽어 디스크에 동기적으로 저장
        # (대용량 파일이면 이 부분도 비동기 I/O(aiofiles) 사용 권장)
        contents = await file.read()
        with open(temp_file_path, "wb") as f:
            f.write(contents)

    except Exception as e:
        print(f"🔴 파일 저장 실패: {e}")
        raise HTTPException(status_code=500, detail=f"파일을 임시 저장하는 데 실패했습니다: {e}")

    # 3. Job을 'pending' 상태로 DB(Redis)에 생성
    # (metadata가 있다면 여기서 함께 전달)
    if not job_manager.create_job(job_id, metadata={"filename": file.filename}):
        raise HTTPException(status_code=500, detail="Job을 생성하는데 실패했습니다 (Redis 연결 확인)")

    # 4. (★핵심) 백그라운드 작업 예약
    # worker.py의 run_stt_and_summary_pipeline 함수를 호출
    worker.run_stt_and_summary_pipeline.delay(
        job_id,
        temp_file_path
    )

    # 5. (명세서 F-API-01) 즉시 응답 반환
    return {
        "job_id": job_id,
        "status": "pending",
        "message": "작업이 성공적으로 요청되었습니다."
    }


# (F-API-02) 처리 상태 및 결과 조회 (Polling)
@app.get("/api/v1/conversation/result/{job_id}")
def get_conversation_result(job_id: str):
    """
    `job_id`를 사용하여 작업의 현재 상태와 
    중간(STT) 또는 최종(요약) 결과를 조회합니다.
    """

    # 1. DB(Redis)에서 Job 정보 조회
    job = job_manager.get_job(job_id)

    # 2. Job이 없는 경우 404
    if not job:
        raise HTTPException(status_code=404, detail="Job ID를 찾을 수 없습니다.")

    # 3. (명세서 F-API-02) 상태별로 다른 응답 반환
    status = job.get("status")

    if status == "completed":
        # (상태 3) 모든 작업 완료
        return {
            "job_id": job_id,
            "status": "completed",
            "original_transcript": job.get("original_transcript"),
            "structured_summary": job.get("structured_summary"),
            "metadata": job.get("metadata")
        }

    elif status == "transcribed":
        # (상태 2) STT 완료, 요약 진행 중
        return {
            "job_id": job_id,
            "status": "transcribed",
            "original_transcript": job.get("original_transcript")
        }

    elif status == "failed":
        # (상태 4) 작업 실패
        return {
            "job_id": job_id,
            "status": "failed",
            "error_message": job.get("error_message")
        }

    else:  # (status == "pending" or status == "processing")
        # (상태 1) 처리 중
        return {
            "job_id": job_id,
            "status": status
        }