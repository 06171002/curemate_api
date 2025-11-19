import os
import sys
import traceback
from typing import Dict, Any

from patient_api.services.stt import whisper_service
from patient_api.services.llm import llm_service
from patient_api.services.storage import job_manager, JobStatus


async def run_batch_pipeline(job_id: str, audio_file_path: str) -> Dict[str, Any]:
    """
    배치 작업 파이프라인: STT → 요약

    Args:
        job_id: 작업 ID
        audio_file_path: 오디오 파일 경로

    Returns:
        최종 결과 딕셔너리
    """
    print(f"[BatchPipeline] 🔵 작업 시작 (Job: {job_id})")

    try:
        # ========== 1. PROCESSING 상태 ==========
        job_manager.update_status(job_id, JobStatus.PROCESSING)

        # ========== 2. STT 실행 ==========
        print(f"[BatchPipeline] 🎤 STT 시작...")

        transcript_segments = []
        segment_count = 0

        try:
            for segment in whisper_service.transcribe_audio_streaming(audio_file_path):
                segment_count += 1
                transcript_segments.append(segment)

                # (선택) DB에 세그먼트 저장
                # db_service.insert_stt_segment(job_id, segment)

                # 실시간 세그먼트 발행
                job_manager.publish_event(job_id, {
                    "type": "transcript_segment",
                    "text": segment,
                    "segment_number": segment_count
                })

        except Exception as stt_error:
            error_msg = f"STT 오류: {str(stt_error)}"
            stack_trace = traceback.format_exc()

            print(f"[BatchPipeline] 🔴 {error_msg}", file=sys.stderr)
            job_manager.log_error(job_id, "batch_stt", f"{error_msg}\n\n{stack_trace}")
            raise

        # ========== 3. TRANSCRIBED 상태 ==========
        full_transcript = " ".join(transcript_segments)

        if not full_transcript:
            warning_msg = "STT 결과 없음"
            print(f"[BatchPipeline] ⚠️ {warning_msg}")

            job_manager.update_status(
                job_id,
                JobStatus.TRANSCRIBED,
                transcript="",
                error_message=warning_msg
            )
            return {"status": "transcribed", "error": warning_msg}

        job_manager.update_status(
            job_id,
            JobStatus.TRANSCRIBED,
            transcript=full_transcript,
            segment_count=segment_count
        )

        print(f"[BatchPipeline] ✅ STT 완료 ({segment_count}개)")

        # ========== 4. 요약 실행 ==========
        print(f"[BatchPipeline] 🤖 요약 시작...")

        try:
            summary_dict = await llm_service.get_summary(full_transcript)

        except Exception as summary_error:
            error_msg = f"요약 오류: {str(summary_error)}"
            stack_trace = traceback.format_exc()

            print(f"[BatchPipeline] 🔴 {error_msg}", file=sys.stderr)
            job_manager.log_error(job_id, "batch_summary", f"{error_msg}\n\n{stack_trace}")

            return {
                "status": "transcribed",
                "transcript": full_transcript,
                "error": error_msg
            }

        # ========== 5. 요약 결과 발행 ==========
        job_manager.publish_event(job_id, {
            "type": "final_summary",
            "summary": summary_dict,
            "segment_count": segment_count
        })

        # ========== 6. COMPLETED 상태 ==========
        job_manager.update_status(
            job_id,
            JobStatus.COMPLETED,
            summary=summary_dict
        )

        print(f"[BatchPipeline] 🟢 작업 완료")

        return {
            "status": "completed",
            "transcript": full_transcript,
            "summary": summary_dict,
            "segment_count": segment_count
        }

    except Exception as e:
        # ========== 7. FAILED 상태 ==========
        error_msg = f"파이프라인 실패: {str(e)}"
        stack_trace = traceback.format_exc()

        print(f"[BatchPipeline] 🔴 {error_msg}", file=sys.stderr)

        job_manager.log_error(job_id, "batch_pipeline", f"{error_msg}\n\n{stack_trace}")
        job_manager.update_status(job_id, JobStatus.FAILED, error_message=error_msg)

        return {"status": "failed", "error": error_msg}

    finally:
        # ========== 8. 임시 파일 삭제 ==========
        if os.path.exists(audio_file_path):
            try:
                os.remove(audio_file_path)
                print(f"[BatchPipeline] 🗑️ 임시 파일 삭제")
            except Exception as e:
                print(f"[BatchPipeline] ⚠️ 파일 삭제 실패: {e}")