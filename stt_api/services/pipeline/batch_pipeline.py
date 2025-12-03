import os
import sys
import traceback
from typing import Dict, Any

from stt_api.services.stt import whisper_service
from stt_api.services.llm import llm_service
from stt_api.services.storage import job_manager, JobStatus
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


async def run_batch_pipeline(job_id: str, audio_file_path: str) -> Dict[str, Any]:
    """
    배치 작업 파이프라인: STT → 요약

    변경사항:
    - 세그먼트 반환 시 현재 상태(status)를 함께 반환합니다.
    - 진행 중인 세그먼트: PROCESSING
    - 마지막 세그먼트: TRANSCRIBED
    - 요약 완료: COMPLETED
    """
    logger.info("[BatchPipeline] 작업 시작", job_id=job_id)

    try:
        # ========== 1. PROCESSING 상태 ==========
        await job_manager.update_status(job_id, JobStatus.PROCESSING)

        # ✅ Job의 metadata에서 mode 가져오기
        job = await job_manager.get_job(job_id)
        mode = job.get("metadata", {}).get("mode")

        # ✅ mode에 따라 서비스 선택
        if mode == "google":
            from stt_api.services.stt.stt_factory import get_stt_service
            from stt_api.services.llm import get_llm_service

            stt = get_stt_service(mode="google")
            llm = get_llm_service(mode="google")

            logger.info("[BatchPipeline] Google 모드로 실행")
        else:
            from stt_api.services.stt import whisper_service as stt
            from stt_api.services.llm import llm_service as llm

            logger.info("[BatchPipeline] 기본 모드로 실행")

        # ========== 2. STT 실행 (상태 포함 반환 로직 적용) ==========
        logger.info("[BatchPipeline] STT 시작...")

        transcript_segments = []
        segment_count = 0

        try:
            # 1. 제너레이터 생성
            stt_generator = whisper_service.transcribe_audio_streaming(audio_file_path)

            # 2. 첫 번째 세그먼트 미리 가져오기
            try:
                current_segment = next(stt_generator)
            except StopIteration:
                current_segment = None

            if current_segment:
                segment_count = 1

                while True:
                    try:
                        # 3. 다음 세그먼트가 있는지 확인 (Look-ahead)
                        next_segment = next(stt_generator)

                        # [다음 세그먼트가 있음] -> 현재 세그먼트는 "진행 중(PROCESSING)"
                        transcript_segments.append(current_segment)

                        await job_manager.save_segment(
                            job_id=job_id,
                            segment_text=current_segment,
                            start_time=None,
                            end_time=None
                        )

                        job_manager.publish_event(job_id, {
                            "type": "transcript_segment",
                            "text": current_segment,
                            "segment_number": segment_count,
                            "status": JobStatus.PROCESSING.value  # ✅ 진행 중 상태
                        })

                        # 포인터 이동
                        current_segment = next_segment
                        segment_count += 1

                    except StopIteration:
                        # [다음 세그먼트가 없음] -> 현재 세그먼트가 "마지막(TRANSCRIBED)"
                        transcript_segments.append(current_segment)

                        await job_manager.save_segment(
                            job_id=job_id,
                            segment_text=current_segment,
                            start_time=None,
                            end_time=None
                        )

                        # ✅ 마지막 세그먼트 반환 시 TRANSCRIBED 상태 전달
                        job_manager.publish_event(job_id, {
                            "type": "transcript_segment",
                            "text": current_segment,
                            "segment_number": segment_count,
                            "status": JobStatus.TRANSCRIBED.value
                        })
                        break

        except Exception as stt_error:
            error_msg = f"STT 오류: {str(stt_error)}"
            stack_trace = traceback.format_exc()

            logger.error("[BatchPipeline]", error_msg=error_msg)
            await job_manager.log_error(job_id, "batch_stt", f"{error_msg}\n\n{stack_trace}")
            raise

        # ========== 3. TRANSCRIBED 상태 (DB 업데이트) ==========
        full_transcript = " ".join(transcript_segments)

        if not full_transcript:
            warning_msg = "STT 결과 없음"
            logger.warning("[BatchPipeline]", warning_msg=warning_msg)

            await job_manager.update_status(
                job_id,
                JobStatus.COMPLETED,
                transcript="",
                error_message=warning_msg
            )
            return {"status": "completed", "error": warning_msg}

        await job_manager.update_status(
            job_id,
            JobStatus.TRANSCRIBED,
            transcript=full_transcript,
            segment_count=segment_count
        )

        logger.info("[BatchPipeline] STT 완료", segment_count=segment_count)

        # ========== 4. 요약 실행 ==========
        logger.info("[BatchPipeline] 요약 시작...")

        try:
            summary_dict = await llm_service.get_summary(full_transcript)

        except Exception as summary_error:
            error_msg = f"요약 오류: {str(summary_error)}"
            stack_trace = traceback.format_exc()

            logger.error("[BatchPipeline]", error_msg=error_msg)
            await job_manager.log_error(job_id, "batch_summary", f"{error_msg}\n\n{stack_trace}")

            return {
                "status": "transcribed",
                "transcript": full_transcript,
                "error": error_msg
            }

        # ========== 5. 요약 결과 발행 (COMPLETED 상태) ==========
        job_manager.publish_event(job_id, {
            "type": "final_summary",
            "summary": summary_dict,
            "segment_count": segment_count,
            "status": JobStatus.COMPLETED.value  # ✅ 완료 상태
        })

        # ========== 6. COMPLETED 상태 (DB 업데이트) ==========
        await job_manager.update_status(
            job_id,
            JobStatus.COMPLETED,
            summary=summary_dict
        )

        logger.info("[BatchPipeline] 작업 완료")

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

        logger.error("[BatchPipeline]", error_msg=error_msg)

        await job_manager.log_error(job_id, "batch_pipeline", f"{error_msg}\n\n{stack_trace}")
        await job_manager.update_status(job_id, JobStatus.COMPLETED, error_message=error_msg)

        return {"status": "failed", "error": error_msg}

    finally:
        # ========== 8. 임시 파일 삭제 ==========
        if os.path.exists(audio_file_path):
            try:
                os.remove(audio_file_path)
                logger.info("[BatchPipeline] 임시 파일 삭제")
            except Exception as e:
                logger.error("[BatchPipeline] 파일 삭제 실패", error_msg=e)