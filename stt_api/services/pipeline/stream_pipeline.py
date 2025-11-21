import asyncio
from typing import AsyncGenerator, Dict, Any

from stt_api.services.stt import transcribe_segment_from_bytes
from stt_api.services.llm import llm_service
from stt_api.services.storage import job_manager, JobStatus
from stt_api.domain.streaming_job import StreamingJob
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


class StreamPipeline:
    """실시간 스트리밍 파이프라인"""

    def __init__(self, job: StreamingJob):
        self.job = job
        self.segment_count = 0

    async def process_audio_chunk(self, audio_chunk: bytes) -> AsyncGenerator[Dict[str, Any], None]:
        """
        오디오 청크 처리 및 실시간 결과 반환

        Args:
            audio_chunk: 오디오 바이트 청크

        Yields:
            {"type": "transcript_segment", "text": "...", "segment_number": N}
        """
        # VAD로 세그먼트 감지
        segment_bytes = self.job.process_audio_chunk(audio_chunk)

        if segment_bytes:
            self.segment_count += 1

            try:
                # STT 처리
                segment_text = await asyncio.to_thread(
                    transcribe_segment_from_bytes,
                    segment_bytes,
                    self.job.current_prompt_context
                )

                if segment_text:
                    # Job의 문맥 업데이트
                    self.job.current_prompt_context += " " + segment_text
                    self.job.full_transcript.append(segment_text)

                    # 실시간 결과 반환
                    yield {
                        "type": "transcript_segment",
                        "text": segment_text,
                        "segment_number": self.segment_count
                    }

                    logger.info(
                        "세그먼트 처리 완료",
                        segment_number=self.segment_count,
                        text_preview=segment_text[:30]
                    )

            except Exception as e:
                error_msg = f"STT 오류: {str(e)}"
                logger.error(
                    "세그먼트 STT 처리 실패",
                    exc_info=True,
                    segment_number=self.segment_count,
                    error=str(e)
                )

                job_manager.log_error(self.job.job_id, "stream_stt", error_msg)

                yield {
                    "type": "error",
                    "message": error_msg
                }

    async def finalize(self) -> Dict[str, Any]:
        """
        스트림 종료 시 최종 요약 생성

        Returns:
            {"type": "final_summary", "summary": {...}, "total_segments": N}
        """
        final_transcript = self.job.get_full_transcript()

        if not final_transcript:
            logger.warning(
                "대화 내용 없음",
                job_id=self.job.job_id,
                segment_count=self.segment_count
            )

            job_manager.update_status(
                self.job.job_id,
                JobStatus.TRANSCRIBED,
                transcript="",
                error_message="대화 내용 없음"
            )

            return {
                "type": "error",
                "message": "대화 내용이 없습니다"
            }

        try:
            # STT 완료
            job_manager.update_status(
                self.job.job_id,
                JobStatus.TRANSCRIBED,
                transcript=final_transcript
            )

            logger.info(
                "STT 완료",
                job_id=self.job.job_id,
                segment_count=self.segment_count
            )

            # 요약 시작
            logger.info("요약 시작", job_id=self.job.job_id)
            summary_dict = await llm_service.get_summary(final_transcript)

            # 완료 상태
            job_manager.update_status(
                self.job.job_id,
                JobStatus.COMPLETED,
                summary=summary_dict
            )

            logger.info("요약 완료", job_id=self.job.job_id)

            return {
                "type": "final_summary",
                "summary": summary_dict,
                "total_segments": self.segment_count
            }

        except Exception as e:
            error_msg = f"요약 오류: {str(e)}"
            logger.error(
                "요약 처리 실패",
                exc_info=True,
                job_id=self.job.job_id,
                error=str(e)
            )

            job_manager.log_error(self.job.job_id, "stream_summary", error_msg)

            return {
                "type": "error",
                "message": error_msg
            }