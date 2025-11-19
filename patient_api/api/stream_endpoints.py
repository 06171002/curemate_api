# patient_api/api/stream_endpoints.py (개선 버전)
import sys

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    HTTPException
)
import asyncio
import traceback
from patient_api.domain.streaming_job import StreamingJob
from patient_api.services.pipeline import StreamPipeline
from patient_api.services.storage import job_manager, JobType, JobStatus
from patient_api.services.llm import lm_service
from patient_api.services.stt import whisper_service
from patient_api.core.config import active_jobs

router = APIRouter()


@router.post("/api/v1/stream/create", status_code=201)
def create_stream_job():
    """
    (F-API-03) 실시간 화상 통화를 위한 StreamingJob을 생성합니다.
    """
    # 1. StreamingJob 인스턴스 생성
    job = StreamingJob(metadata={})

    # 2. 전역 매니저(dict)에 등록
    active_jobs[job.job_id] = job

    # ✅ JobManager로 작업 생성
    if not job_manager.create_job(job.job_id, JobType.REALTIME, metadata=job.metadata):
        del active_jobs[job.job_id]
        raise HTTPException(status_code=500, detail="작업 생성 실패")

    print(f"[JobManager] 🟢 새 스트림 작업 생성됨 (Job ID: {job.job_id})")
    return {
        "job_id": job.job_id,
        "job_type": "REALTIME",
        "status": "pending"
    }


@router.websocket("/ws/v1/stream/{job_id}")
async def conversation_stream(websocket: WebSocket, job_id: str):
    """
    (F-API-04) job_id에 해당하는 스트림 작업을 찾아 WebSocket을 연결합니다.
    """

    # 1. 매니저에서 Job 인스턴스 조회
    job = active_jobs.get(job_id)

    if not job:
        print(f"[WebSocket] 🔴 존재하지 않는 Job ID로 연결 시도: {job_id}")
        await websocket.close(code=1008, reason="Job ID not found")
        job_manager.log_error(job_id, "websocket_stream", "존재하지 않는 Job ID")
        return

    # 2. 연결 수락
    await websocket.accept()
    print(f"[WebSocket] 🟢 클라이언트 연결됨 (Job: {job_id})")

    # ✅ JobManager로 상태 업데이트
    job_manager.update_status(job_id, JobStatus.PROCESSING)

    await websocket.send_json({
        "type": "connection_success",
        "message": f"Job {job_id}에 성공적으로 연결되었습니다."
    })

    # ✅ Pipeline 사용
    pipeline = StreamPipeline(job)

    try:
        # --- 실시간 VAD/STT 처리 루프 ---
        while True:
            audio_chunk = await websocket.receive_bytes()

            # ✅ Pipeline로 처리
            async for result in pipeline.process_audio_chunk(audio_chunk):
                await websocket.send_json(result)

    except WebSocketDisconnect:
        print(f"[WebSocket] 🟡 클라이언트 연결 끊김 (Job: {job_id})")

        # ✅ Pipeline로 최종 처리
        final_result = await pipeline.finalize()

        try:
            await websocket.send_json(final_result)
        except:
            pass  # 이미 연결이 끊긴 경우 무시


    except Exception as e:
        error_msg = f"예기치 않은 오류: {str(e)}"

        print(f"[WebSocket] 🔴 {error_msg}")

        job_manager.log_error(job_id, "websocket", error_msg)

        job_manager.update_status(job_id, JobStatus.FAILED, error_message=error_msg)

    finally:
        # 전역 매니저에서 Job 제거 (메모리 누수 방지!)
        if job_id in active_jobs:
            del active_jobs[job_id]
            print(f"[JobManager] 🗑️  스트림 작업 제거됨 (메모리 정리): {job_id}")