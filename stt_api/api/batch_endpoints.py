import os
import uuid
import json
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    HTTPException,
    Request,
    Query
)
from typing import Optional
from sse_starlette.sse import EventSourceResponse
from stt_api.services.storage import job_manager, JobType, JobStatus
from stt_api.services import tasks
from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


router = APIRouter()


@router.post("/api/v1/conversation/request", status_code=202)
async def create_conversation_request(
        file: UploadFile = File(...),
        cure_seq: Optional[int] = Form(None, description="치료 ID"),
        cust_seq: Optional[int] = Form(None, description="보호자 ID"),
        patient_seq: Optional[int] = Form(None, description="환자 ID"),
        mode: Optional[str] = Form(None, description="google 또는 None")
):
    """
    음성 파일 업로드 및 STT 작업 생성

    Request Body (multipart/form-data):
        - file: 음성 파일 (mp3, wav, m4a)
        - cure_seq: 큐어룸 고유번호
        - cust_seq: 고객 고유번호
    """
    job_id = str(uuid.uuid4())

    # 1. 파일 저장
    try:
        file_ext = file.filename.split(".")[-1]
        temp_file_path = os.path.join(settings.TEMP_AUDIO_DIR, f"{job_id}.{file_ext}")
        contents = await file.read()
        with open(temp_file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        logger.error("파일 저장 실패", error_messag=e)
        raise HTTPException(
            status_code=500,
            detail=f"파일을 임시 저장하는 데 실패했습니다: {e}"
        )

    # 2. DB에 BATCH 작업 생성 (우선순위 1)
    metadata = {
        "filename": file.filename,
        "file_size": len(contents),
        "file_path": temp_file_path,
        # 👇 여기에 도메인 종속 데이터를 넣습니다.
        "cure_seq": cure_seq,
        "cust_seq": cust_seq,
        "patient_seq": patient_seq,
        "mode": mode,
    }

    # 3. DB에 작업 생성 (✅ cure_seq, cust_seq 별도 전달)
    try:
        success = await job_manager.create_job(
            job_id, JobType.BATCH, metadata=metadata
        )

        if not success:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            raise HTTPException(status_code=500, detail="작업 생성 실패")

    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        raise HTTPException(status_code=500, detail=f"작업 생성 실패: {str(e)}")

    # 3. Celery Task 백그라운드 작업 예약
    try:
        tasks.run_stt_and_summary_pipeline.delay(job_id, temp_file_path)
        logger.info("작업 생성 완료", job_id=job_id)
    except Exception as e:
        error_msg = f"Celery 작업 예약 실패: {str(e)}"
        logger.error("Celery 작업 예약 실패", error_msg=e)

        # ✅ JobManager로 실패 상태 업데이트 (await 추가)
        await job_manager.update_status(job_id, JobStatus.COMPLETED, error_message=error_msg)
        await job_manager.log_error(job_id, "celery_task", error_msg)

        raise HTTPException(status_code=500, detail=error_msg)

    return {
        "job_id": job_id,
        "job_type": "BATCH",
        "status": "pending",
        "message": "작업이 성공적으로 요청되었습니다."
    }


@router.get("/api/v1/conversation/result/{job_id}")
async def get_conversation_result(job_id: str):  # ✅ async def로 변경
    # ✅ JobManager로 조회 (DB → Redis 자동 폴백) - await 추가
    job = await job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job ID를 찾을 수 없습니다.")

    return job


@router.get("/api/v1/conversation/stream-events/{job_id}")
async def stream_events(job_id: str, request: Request):
    """
    ✅ 개선된 SSE 스트리밍:
    1. 연결 시 이미 처리된 세그먼트를 먼저 전송
    2. 이후 실시간 이벤트 구독
    """
    # 1. Job 존재 확인 - ✅ await 추가
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job ID를 찾을 수 없습니다.")

    # ✅ 현재 작업 상태 미리 확보
    current_job_status = job.get("status", "PENDING")

    async def event_generator():
        try:
            # ✅ STEP 1: 과거 세그먼트 전송 (DB에서 조회) - await 추가
            past_segments = await job_manager.get_segments(job_id)

            logger.info(
                "[SSE] 과거 세그먼트 전송 시작",
                job_id=job_id,
                count=len(past_segments)
            )

            for segment in past_segments:
                if await request.is_disconnected():
                    logger.info("[SSE] 클라이언트 연결 끊김 (과거 데이터 전송 중)")
                    return

                yield {
                    "event": "transcript_segment",
                    "data": json.dumps({
                        "type": "transcript_segment",
                        "text": segment["segment_text"],
                        "segment_number": segment.get("segment_number", 0),
                        "is_historical": True,  # ✅ 과거 데이터 표시
                        "status": current_job_status
                    })
                }

            # ✅ STEP 2: 현재 상태 확인 (이미 완료된 경우 final_summary 전송)
            current_status = job.get("status", "").upper()

            if current_status == "COMPLETED":
                logger.info("[SSE] 작업이 이미 완료됨, final_summary 전송")

                yield {
                    "event": "final_summary",
                    "data": json.dumps({
                        "type": "final_summary",
                        "summary": job.get("structured_summary", {}),
                        "segment_count": len(past_segments),
                        "is_historical": True,
                        "status": "COMPLETED"
                    })
                }
                return  # 스트림 종료

            # ✅ STEP 3: 실시간 이벤트 구독 (진행 중인 경우)
            logger.info("[SSE] 실시간 이벤트 구독 시작", job_id=job_id)

            async for message_data in job_manager.subscribe_events(job_id):
                if await request.is_disconnected():
                    logger.info("[SSE] 클라이언트 연결 끊김 (실시간 구독 중)")
                    break

                event_type = message_data.get("type", "message")
                data_json = json.dumps(message_data)

                yield {
                    "event": event_type,
                    "data": data_json
                }

                # 최종 요약 수신 시 스트림 종료
                if event_type == "final_summary":
                    logger.info("[SSE] 최종 요약 전송 완료, 스트림 종료")
                    break

        except Exception as e:
            error_msg = f"스트리밍 중 오류: {str(e)}"
            logger.error("스트리밍 중 오류", error_msg=e)

            # ✅ JobManager로 에러 로깅 (await 추가)
            await job_manager.log_error(job_id, "sse_stream", error_msg)

            yield {
                "event": "error",
                "data": json.dumps({"message": error_msg})
            }

    return EventSourceResponse(event_generator())


@router.get("/api/v1/conversation/errors/{job_id}")
async def get_job_errors(job_id: str):  # ✅ async def로 변경
    """
    특정 작업의 에러 로그를 조회합니다.
    """
    # ✅ JobManager로 에러 로그 조회 (await 추가)
    errors = await job_manager.get_errors(job_id)

    if not errors:
        # Job 자체가 존재하는지 확인 (await 추가)
        job_exists = await job_manager.get_job(job_id)
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