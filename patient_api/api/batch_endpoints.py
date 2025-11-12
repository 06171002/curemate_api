import os
import uuid
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException
)
# (★중요) refactoring된 경로로 임포트
from patient_api.repositories import job_repository
from patient_api.services import tasks
# (★수정) main.py 대신 core.config에서 임포트
from patient_api.core.config import TEMP_AUDIO_DIR

# "컨트롤러" 역할을 할 APIRouter 객체 생성
router = APIRouter()

# (F-API-01) 대화 내용 처리 요청 (비동기 작업 생성)
@router.post("/api/v1/conversation/request", status_code=202)
async def create_conversation_request(
        file: UploadFile = File(...)
):
    """
    음성 파일(mp3, wav, m4a 등)을 업로드하여
    STT 및 요약 작업을 **백그라운드에서 시작**시킵니다.
    """
    job_id = str(uuid.uuid4())
    try:
        file_ext = file.filename.split(".")[-1]
        temp_file_path = os.path.join(TEMP_AUDIO_DIR, f"{job_id}.{file_ext}")
        contents = await file.read()
        with open(temp_file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        print(f"🔴 파일 저장 실패: {e}")
        raise HTTPException(status_code=500, detail=f"파일을 임시 저장하는 데 실패했습니다: {e}")

    # (F-DB-01) Redis에 Job 생성
    if not job_repository.create_job(job_id, metadata={"filename": file.filename}):
        raise HTTPException(status_code=500, detail="Job을 생성하는데 실패했습니다 (Redis 연결 확인)")

    # (Celery Task) 백그라운드 작업 예약
    tasks.run_stt_and_summary_pipeline.delay(
        job_id,
        temp_file_path
    )

    return {
        "job_id": job_id,
        "status": "pending",
        "message": "작업이 성공적으로 요청되었습니다."
    }

# (F-API-02) 처리 상태 및 결과 조회 (Polling)
@router.get("/api/v1/conversation/result/{job_id}")
def get_conversation_result(job_id: str):
    """
    `job_id`를 사용하여 작업의 현재 상태와
    중간(STT) 또는 최종(요약) 결과를 조회합니다.
    """
    job = job_repository.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job ID를 찾을 수 없습니다.")

    status = job.get("status")

    if status == "completed":
        return job
    elif status == "transcribed":
        return {
            "job_id": job_id,
            "status": status,
            "original_transcript": job.get("original_transcript")
        }
    elif status == "failed":
        return {"job_id": job_id, "status": status, "error_message": job.get("error_message")}
    else:
        return {"job_id": job_id, "status": status}