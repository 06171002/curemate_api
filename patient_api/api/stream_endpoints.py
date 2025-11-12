from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    HTTPException
)
# (★중요) refactoring된 경로로 임포트
from patient_api.domain.streaming_job import StreamingJob
from patient_api.repositories import job_repository
from patient_api.services import ollama_service, stt_service
# (★수정) main.py 대신 core.config에서 임포트
from patient_api.core.config import active_jobs

router = APIRouter()


# (F-API-03) 실시간 스트림 작업 생성
@router.post("/api/v1/stream/create", status_code=201)
def create_stream_job():
    """
    (F-API-03) 실시간 화상 통화를 위한 StreamingJob을 생성합니다.
    """
    # 1. (F-JOB-01) StreamingJob 인스턴스 생성
    job = StreamingJob(metadata={})

    # 2. (F-JOB-02) 전역 매니저(dict)에 등록
    active_jobs[job.job_id] = job

    # 3. (F-DB-01) Redis에도 'pending' 레코드 생성 (히스토리 저장용)
    if not job_repository.create_job(job.job_id, job.metadata):
        del active_jobs[job.job_id]
        raise HTTPException(status_code=500, detail="Job을 Redis에 생성하는데 실패했습니다.")

    print(f"[JobManager] 🟢 새 스트림 작업 생성됨 (Job ID: {job.job_id})")
    return {"job_id": job.job_id}


# (F-API-04) 실시간 STT 스트리밍 (테스트용 뼈대)
@router.websocket("/ws/v1/stream/{job_id}")
async def conversation_stream(websocket: WebSocket, job_id: str):
    """
    (F-API-04) job_id에 해당하는 스트림 작업을 찾아 WebSocket을 연결합니다.
    """

    # 1. (F-JOB-02) 매니저에서 Job 인스턴스 조회
    job = active_jobs.get(job_id)

    if not job:
        print(f"[WebSocket] 🔴 존재하지 않는 Job ID로 연결 시도: {job_id}")
        await websocket.close(code=1008, reason="Job ID not found")
        return

    # 2. 연결 수락
    await websocket.accept()
    print(f"[WebSocket] 🟢 클라이언트 연결됨 (Job: {job_id})")

    await websocket.send_json({
        "type": "connection_success",
        "message": f"Job {job_id}에 성공적으로 연결되었습니다."
    })

    try:
        # --- (★나중에 VAD/STT 로직으로 교체될 부분) ---
        while True:
            audio_chunk = await websocket.receive_bytes()

            # (F-VAD-01) VAD 프로세서에 청크 전달
            segment_detected = job.process_audio_chunk(audio_chunk)

            if segment_detected:
                # (테스트용) VAD가 세그먼트를 감지했다고 클라이언트에 알림
                await websocket.send_json({
                    "type": "vad_segment_detected",
                    "message": "음성 세그먼트가 감지되었습니다. (STT 처리 시작)"
                })
                # (★실제 구현 시) 여기서 stt_service.transcribe_segment_... 호출

    except WebSocketDisconnect:
        print(f"[WebSocket] 🟡 클라이언트 연결 끊김 (Job: {job_id})")
        # (F-SUM-04) (★나중에 요약 및 DB 저장 로직 추가)
        # final_transcript = job.get_full_transcript()
        # summary = await ollama_service.get_summary(final_transcript)
        # job_repository.update_job(job.job_id, {"status": "completed", ...})

    except Exception as e:
        print(f"[WebSocket] 🔴 예기치 않은 오류: {e}")

    finally:
        # (F-JOB-02) 매니저(dict)에서 Job 제거 (메모리 누수 방지!)
        if job_id in active_jobs:
            del active_jobs[job_id]
            print(f"[JobManager] 🔴 스트림 작업 제거됨 (메모리 정리): {job_id}")