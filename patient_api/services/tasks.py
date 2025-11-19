# patient_api/services/tasks.py

import os
import sys
import asyncio
import traceback
from patient_api.repositories import job_repository
from patient_api.services import ollama_service, stt_service, lm_service
from patient_api.services.database_service import db_service
from patient_api.core.celery_config import celery_app


async def _run_pipeline_async(job_id: str, audio_file_path: str):
    """
    백그라운드 작업의 메인 파이프라인.
    STT -> 요약 순서로 처리하며, 각 단계마다 DB와 Pub/Sub 업데이트
    """
    print(f"[Worker] 🔵 작업 시작 (Job ID: {job_id}, File: {audio_file_path})")

    try:
        # ========== 1. 상태 변경: PROCESSING ==========
        db_service.update_stt_job_status(job_id, "PROCESSING")
        job_repository.update_job(job_id, {"status": "processing"})

        # ========== 2. STT 실행 (스트리밍) ==========
        print(f"[Worker] (Job {job_id}) STT 작업 시작...")

        transcript_segments = []
        segment_count = 0

        try:
            for segment in stt_service.transcribe_audio_streaming(audio_file_path):
                segment_count += 1
                transcript_segments.append(segment)

                # (선택) DB에 세그먼트 저장
                # db_service.insert_stt_segment(job_id, segment)

                # Pub/Sub으로 실시간 세그먼트 발행
                message_data = {
                    "type": "transcript_segment",
                    "text": segment,
                    "segment_number": segment_count
                }
                job_repository.publish_message(job_id, message_data)

        except Exception as stt_error:
            error_msg = f"STT 처리 중 오류: {str(stt_error)}"
            stack_trace = traceback.format_exc()

            print(f"[Worker] 🔴 {error_msg}", file=sys.stderr)
            print(f"[Worker] 🔴 스택 트레이스:\n{stack_trace}", file=sys.stderr)

            # DB 에러 로그
            db_service.log_error(job_id, "celery_stt", f"{error_msg}\n\n{stack_trace}")

            # 예외를 다시 발생시켜 외부 except 블록에서 처리
            raise

        # ========== 3. STT 완료 상태 저장: TRANSCRIBED ==========
        full_transcript = " ".join(transcript_segments)

        if not full_transcript:
            warning_msg = "STT 결과가 비어있습니다 (음성 감지 실패)"
            print(f"[Worker] ⚠️ (Job {job_id}) {warning_msg}")

            db_service.update_stt_job_status(
                job_id,
                "TRANSCRIBED",
                transcript="",
                error_message=warning_msg
            )

            job_repository.update_job(job_id, {
                "status": "transcribed",
                "original_transcript": "",
                "error_message": warning_msg
            })

            # 요약 건너뛰고 종료
            return

        db_service.update_stt_job_status(
            job_id,
            "TRANSCRIBED",
            transcript=full_transcript
        )

        job_repository.update_job(job_id, {
            "status": "transcribed",
            "original_transcript": full_transcript,
            "segment_count": segment_count
        })

        print(f"[Worker] ✅ (Job {job_id}) STT 완료 (총 {segment_count}개 세그먼트)")

        # ========== 4. 요약 실행 ==========
        print(f"[Worker] (Job {job_id}) 요약 작업 시작...")

        try:
            # summary_dict = await ollama_service.get_summary(full_transcript)
            summary_dict = await lm_service.get_summary(full_transcript)

        except Exception as summary_error:
            error_msg = f"요약 처리 중 오류: {str(summary_error)}"
            stack_trace = traceback.format_exc()

            print(f"[Worker] 🔴 {error_msg}", file=sys.stderr)
            print(f"[Worker] 🔴 스택 트레이스:\n{stack_trace}", file=sys.stderr)

            # DB 에러 로그
            db_service.log_error(job_id, "celery_summary", f"{error_msg}\n\n{stack_trace}")

            # STT는 성공했으므로 TRANSCRIBED 상태 유지하고 종료
            # (요약 실패는 치명적이지 않음)
            return

        # ========== 5. 요약 결과 Pub/Sub 발행 ==========
        summary_message = {
            "type": "final_summary",
            "summary": summary_dict,
            "segment_count": segment_count
        }
        job_repository.publish_message(job_id, summary_message)

        # ========== 6. 최종 상태 저장: COMPLETED ==========
        db_service.update_stt_job_status(
            job_id,
            "COMPLETED",
            summary=summary_dict
        )

        job_repository.update_job(job_id, {
            "status": "completed",
            "structured_summary": summary_dict
        })

        print(f"[Worker] 🟢 작업 성공 (Job ID: {job_id})")

    except Exception as e:
        # ========== 7. 오류 발생 시: FAILED ==========
        error_msg = f"작업 실패: {str(e)}"
        stack_trace = traceback.format_exc()

        print(f"[Worker] 🔴 (Job ID: {job_id}) {error_msg}", file=sys.stderr)
        print(f"[Worker] 🔴 스택 트레이스:\n{stack_trace}", file=sys.stderr)

        # DB 에러 로그
        db_service.log_error(job_id, "celery_task", f"{error_msg}\n\n{stack_trace}")

        # DB 상태 업데이트
        db_service.update_stt_job_status(
            job_id,
            "FAILED",
            error_message=error_msg
        )

        # Redis 상태 업데이트
        job_repository.update_job(job_id, {
            "status": "failed",
            "error_message": error_msg
        })

    finally:
        # ========== 8. 임시 파일 삭제 ==========
        if os.path.exists(audio_file_path):
            try:
                os.remove(audio_file_path)
                print(f"[Worker] 🗑️  (Job {job_id}) 임시 파일 삭제: {audio_file_path}")
            except Exception as e:
                warning_msg = f"임시 파일 삭제 실패: {e}"
                print(f"[Worker] ⚠️ (Job {job_id}) {warning_msg}", file=sys.stderr)
                db_service.log_error(job_id, "file_cleanup", warning_msg)


@celery_app.task
def run_stt_and_summary_pipeline(job_id: str, audio_file_path: str):
    """
    Celery가 호출할 동기식 래퍼 함수.
    비동기 파이프라인을 asyncio.run()으로 실행합니다.
    """
    try:
        asyncio.run(_run_pipeline_async(job_id, audio_file_path))
    except Exception as e:
        # asyncio.run() 자체가 실패한 경우
        error_msg = f"Asyncio 실행 실패: {str(e)}"
        print(f"[Celery] 🔴 {error_msg}", file=sys.stderr)

        db_service.log_error(job_id, "celery_asyncio", error_msg)
        db_service.update_stt_job_status(job_id, "FAILED", error_message=error_msg)