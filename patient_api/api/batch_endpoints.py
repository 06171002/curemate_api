# patient_api/api/batch_endpoints.py

import os
import uuid
import json
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    Request
)
from sse_starlette.sse import EventSourceResponse
from patient_api.repositories import job_repository
from patient_api.services import tasks
from patient_api.services.database_service import db_service
from patient_api.core.config import TEMP_AUDIO_DIR

router = APIRouter()


@router.post("/api/v1/conversation/request", status_code=202)
async def create_conversation_request(
        file: UploadFile = File(...)
):
    """
    음성 파일(mp3, wav, m4a 등)을 업로드하여
    STT 및 요약 작업을 **백그라운드에서 시작**시킵니다.
    """
    job_id = str(uuid.uuid4())

    # 1. 파일 저장
    try:
        file_ext = file.filename.split(".")[-1]
        temp_file_path = os.path.join(TEMP_AUDIO_DIR, f"{job_id}.{file_ext}")
        contents = await file.read()
        with open(temp_file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        print(f"🔴 파일 저장 실패: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"파일을 임시 저장하는 데 실패했습니다: {e}"
        )

    # 2. DB에 BATCH 작업 생성 (우선순위 1)
    metadata = {
        "filename": file.filename,
        "file_size": len(contents),
        "file_path": temp_file_path
    }

    if not db_service.create_stt_job(job_id, "BATCH", metadata=metadata):
        # DB 실패 시 저장된 파일 삭제
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(
            status_code=500,
            detail="DB에 작업을 생성하는데 실패했습니다"
        )

    # 3. Redis에도 작업 생성 (임시, 하위 호환성)
    if not job_repository.create_job(job_id, metadata=metadata):
        # Redis 실패는 치명적이지 않으므로 경고만
        print(f"[JobManager] ⚠️ Redis 작업 생성 실패 (Job ID: {job_id})")
        db_service.log_error(job_id, "redis_create", "Redis 작업 생성 실패")

    # 4. Celery Task 백그라운드 작업 예약
    try:
        tasks.run_stt_and_summary_pipeline.delay(job_id, temp_file_path)
        print(f"[Batch] 🟢 작업 생성 완료 (Job ID: {job_id})")
    except Exception as e:
        error_msg = f"Celery 작업 예약 실패: {str(e)}"
        print(f"[Batch] 🔴 {error_msg}")

        # DB 상태 업데이트: FAILED
        db_service.update_stt_job_status(job_id, "FAILED", error_message=error_msg)
        db_service.log_error(job_id, "celery_task", error_msg)

        raise HTTPException(status_code=500, detail=error_msg)

    return {
        "job_id": job_id,
        "job_type": "BATCH",
        "status": "pending",
        "message": "작업이 성공적으로 요청되었습니다."
    }


@router.get("/api/v1/conversation/result/{job_id}")
def get_conversation_result(job_id: str):
    """
    `job_id`를 사용하여 작업의 현재 상태와
    중간(STT) 또는 최종(요약) 결과를 조회합니다.

    우선순위: DB > Redis
    """
    # 1. DB에서 조회 시도 (우선순위 1)
    db_job = db_service.get_stt_job(job_id)

    if db_job:
        # DB에 데이터가 있으면 DB 데이터 반환
        return db_job

    # 2. Redis에서 조회 시도 (폴백)
    redis_job = job_repository.get_job(job_id)

    if not redis_job:
        raise HTTPException(status_code=404, detail="Job ID를 찾을 수 없습니다.")

    status = redis_job.get("status")

    # 상태별 응답 구성
    if status == "completed":
        return redis_job
    elif status == "transcribed":
        return {
            "job_id": job_id,
            "status": status,
            "original_transcript": redis_job.get("original_transcript")
        }
    elif status == "failed":
        return {
            "job_id": job_id,
            "status": status,
            "error_message": redis_job.get("error_message")
        }
    else:
        return {"job_id": job_id, "status": status}


@router.get("/api/v1/conversation/stream-events/{job_id}")
async def stream_events(job_id: str, request: Request):
    """
    (SSE) job_id에 해당하는 작업의 STT 세그먼트 및 최종 요약을
    실시간으로 스트리밍합니다.
    """
    # 작업 존재 여부 확인
    job_exists = db_service.get_stt_job(job_id) or job_repository.get_job(job_id)

    if not job_exists:
        raise HTTPException(status_code=404, detail="Job ID를 찾을 수 없습니다.")

    async def event_generator():
        """
        Redis Pub/Sub을 구독하고 메시지를 SSE 형식으로 'yield'합니다.
        """
        try:
            async for message_data in job_repository.subscribe_to_messages(job_id):
                # 클라이언트 연결 확인
                if await request.is_disconnected():
                    print(f"[SSE] (Job {job_id}) 클라이언트 연결 끊김")
                    break

                event_type = message_data.get("type", "message")
                data_json = json.dumps(message_data)

                yield {
                    "event": event_type,
                    "data": data_json
                }

                # 최종 요약 수신 시 스트림 종료
                if event_type == "final_summary":
                    print(f"[SSE] (Job {job_id}) 최종 요약 전송 완료, 스트림 종료")
                    break

        except Exception as e:
            error_msg = f"스트리밍 중 오류: {str(e)}"
            print(f"[SSE] 🔴 (Job {job_id}) {error_msg}")

            # DB 에러 로그
            db_service.log_error(job_id, "sse_stream", error_msg)

            yield {
                "event": "error",
                "data": json.dumps({"message": error_msg})
            }

    return EventSourceResponse(event_generator())


@router.get("/api/v1/conversation/errors/{job_id}")
def get_job_errors(job_id: str):
    """
    특정 작업의 에러 로그를 조회합니다.
    """
    errors = db_service.get_error_logs(job_id)

    if not errors:
        # Job 자체가 존재하는지 확인
        job_exists = db_service.get_stt_job(job_id) or job_repository.get_job(job_id)
        if not job_exists:
            raise HTTPException(status_code=404, detail="Job ID를 찾을 수 없습니다.")

        return {
            "job_id": job_id,
            "errors": [],
            "message": "에러 로그가 없습니다."
        }

    return {
        "job_id": job_id,
        "errors": errors,
        "error_count": len(errors)
    }