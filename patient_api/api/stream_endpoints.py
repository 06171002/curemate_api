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
from patient_api.repositories import job_repository
from patient_api.services import ollama_service, stt_service, lm_service
from patient_api.services.database_service import db_service
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

    # 3. (★중요) DB에 REALTIME 작업 생성 (우선순위 1)
    if not db_service.create_stt_job(job.job_id, "REALTIME", metadata=job.metadata):
        del active_jobs[job.job_id]
        raise HTTPException(status_code=500, detail="DB에 작업을 생성하는데 실패했습니다")

    # 4. Redis에도 'pending' 레코드 생성 (임시, 하위 호환성)
    if not job_repository.create_job(job.job_id, job.metadata):
        # Redis 실패는 치명적이지 않으므로 경고만 출력
        print(f"[JobManager] ⚠️ Redis 작업 생성 실패 (Job ID: {job.job_id})")
        db_service.log_error(job.job_id, "redis_create", "Redis 작업 생성 실패")

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
        db_service.log_error(job_id, "websocket_stream", "존재하지 않는 Job ID")
        return

    # 2. 연결 수락
    await websocket.accept()
    print(f"[WebSocket] 🟢 클라이언트 연결됨 (Job: {job_id})")

    # 3. DB 상태 업데이트: PROCESSING
    db_service.update_stt_job_status(job_id, "PROCESSING")

    await websocket.send_json({
        "type": "connection_success",
        "message": f"Job {job_id}에 성공적으로 연결되었습니다."
    })

    segment_count = 0

    try:
        # --- 실시간 VAD/STT 처리 루프 ---
        while True:
            audio_chunk = await websocket.receive_bytes()

            # VAD가 segment_bytes를 반환 (또는 None)
            segment_bytes = job.process_audio_chunk(audio_chunk)

            if segment_bytes:
                segment_count += 1

                try:
                    # STT 호출
                    segment_text = await asyncio.to_thread(
                        stt_service.transcribe_segment_from_bytes,
                        segment_bytes,
                        job.current_prompt_context
                    )

                    if segment_text:
                        # Job의 문맥과 전체 대화록 업데이트
                        job.current_prompt_context += " " + segment_text
                        job.full_transcript.append(segment_text)

                        # (DB) 세그먼트 기록 (선택사항)
                        # db_service.insert_stt_segment(job_id, segment_text)

                        # (WebSocket) 실시간 전송
                        await websocket.send_json({
                            "type": "transcript_segment",
                            "text": segment_text,
                            "segment_number": segment_count
                        })

                        print(f"[WebSocket] (Job {job_id}) 🎤 세그먼트 {segment_count}: {segment_text[:30]}...")

                except Exception as stt_error:
                    error_msg = f"STT 처리 중 오류: {str(stt_error)}"
                    print(f"[WebSocket] 🔴 {error_msg}", file=sys.stderr)

                    # DB 에러 로그
                    db_service.log_error(job_id, "websocket_stt", error_msg)

                    await websocket.send_json({
                        "type": "error",
                        "message": error_msg
                    })

    except WebSocketDisconnect:
        print(f"[WebSocket] 🟡 클라이언트 연결 끊김 (Job: {job_id})")

        # --- 연결 종료 시 요약 및 저장 ---
        final_transcript = job.get_full_transcript()

        if not final_transcript:
            print(f"[WebSocket] (Job {job_id}) 대화 내용이 없어 요약/저장 스킵.")

            # DB 상태: TRANSCRIBED (내용 없음)
            db_service.update_stt_job_status(
                job_id,
                "TRANSCRIBED",
                transcript="",
                error_message="대화 내용 없음"
            )

        else:
            try:
                # 1. DB 상태: TRANSCRIBED
                db_service.update_stt_job_status(
                    job_id,
                    "TRANSCRIBED",
                    transcript=final_transcript
                )

                print(f"[WebSocket] (Job {job_id}) ✅ STT 완료 (총 {segment_count}개 세그먼트)")

                # 2. 요약 시작
                print(f"[WebSocket] (Job {job_id}) 🤖 요약 시작...")
                summary_dict = await lm_service.get_summary(final_transcript)

                # 3. DB에 최종 결과 저장: COMPLETED
                db_service.update_stt_job_status(
                    job_id,
                    "COMPLETED",
                    summary=summary_dict
                )

                # 4. Redis에도 저장 (임시)
                updates = {
                    "status": "completed",
                    "original_transcript": final_transcript,
                    "structured_summary": summary_dict,
                    "segment_count": segment_count
                }
                job_repository.update_job(job.job_id, updates)

                print(f"[WebSocket] (Job {job_id}) ✅ 요약 완료 및 DB 저장 완료")

                # 5. 클라이언트에게 최종 요약본 전송
                await websocket.send_json({
                    "type": "final_summary",
                    "summary": summary_dict,
                    "total_segments": segment_count
                })

            except Exception as summary_error:
                error_msg = f"요약/저장 중 오류: {str(summary_error)}"
                stack_trace = traceback.format_exc()

                print(f"[WebSocket] 🔴 {error_msg}", file=sys.stderr)
                print(f"[WebSocket] 🔴 스택 트레이스:\n{stack_trace}", file=sys.stderr)

                # DB 에러 로그
                db_service.log_error(job_id, "websocket_summary", f"{error_msg}\n\n{stack_trace}")

                # STT는 성공했지만 요약 실패 -> TRANSCRIBED 유지
                job_repository.update_job(job.job_id, {
                    "status": "transcribed",
                    "original_transcript": final_transcript,
                    "error_message": error_msg,
                    "segment_count": segment_count
                })

    except Exception as unexpected_error:
        error_msg = f"예기치 않은 오류: {str(unexpected_error)}"
        stack_trace = traceback.format_exc()

        print(f"[WebSocket] 🔴 {error_msg}", file=sys.stderr)
        print(f"[WebSocket] 🔴 스택 트레이스:\n{stack_trace}", file=sys.stderr)

        # DB 에러 로그
        db_service.log_error(job_id, "websocket_stream", f"{error_msg}\n\n{stack_trace}")

        # DB 상태: FAILED
        db_service.update_stt_job_status(
            job_id,
            "FAILED",
            error_message=error_msg
        )

    finally:
        # 전역 매니저에서 Job 제거 (메모리 누수 방지!)
        if job_id in active_jobs:
            del active_jobs[job_id]
            print(f"[JobManager] 🗑️  스트림 작업 제거됨 (메모리 정리): {job_id}")