import asyncio
import time
from typing import AsyncGenerator, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from stt_api.services.stt import transcribe_segment_from_bytes
from stt_api.services.llm import llm_service
from stt_api.services.storage import job_manager, JobStatus
from stt_api.domain.streaming_job import StreamingJob
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


class StreamPipeline:
    """실시간 스트리밍 파이프라인 (병렬 처리)"""

    def __init__(self, job: StreamingJob, max_workers: int = 3):
        self.job = job
        self.segment_count = 0

        # ✅ STT 병렬 처리를 위한 ThreadPoolExecutor
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

        # ✅ 처리 중인 세그먼트 큐
        self.processing_queue = asyncio.Queue()
        self.result_queue = asyncio.Queue()

        # ✅ 성능 메트릭 추적
        self.metrics = {
            "total_vad_time": 0.0,
            "total_stt_time": 0.0,
            "total_segments": 0
        }

        # ✅ 백그라운드 STT 워커 시작
        self.worker_task = None
        self.is_running = True

    async def start(self):
        """파이프라인 시작"""
        self.worker_task = asyncio.create_task(self._stt_worker())
        logger.info("STT 워커 시작", job_id=self.job.job_id)

    async def _stt_worker(self):
        """
        ✅ 백그라운드에서 STT 처리하는 워커
        """
        try:
            while self.is_running:
                try:
                    # 큐에서 세그먼트 가져오기 (타임아웃 설정)
                    segment_data = await asyncio.wait_for(
                        self.processing_queue.get(),
                        timeout=1.0
                    )

                    if segment_data is None:  # 종료 신호
                        break

                    segment_bytes, segment_num = segment_data

                    # STT 처리
                    stt_start = time.perf_counter()
                    segment_text = await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        transcribe_segment_from_bytes,
                        segment_bytes,
                        self.job.current_prompt_context
                    )
                    stt_duration = (time.perf_counter() - stt_start) * 1000

                    self.metrics["total_stt_time"] += stt_duration
                    self.metrics["total_segments"] += 1
                    self.metrics["pending_segments"] -= 1

                    # 결과를 결과 큐에 추가
                    await self.result_queue.put({
                        "segment_number": segment_num,
                        "text": segment_text,
                        "duration_ms": stt_duration
                    })

                    logger.debug(
                        "STT 워커 처리 완료",
                        segment_number=segment_num,
                        stt_ms=round(stt_duration, 2),
                        text_preview=segment_text[:30] if segment_text else ""
                    )

                except asyncio.TimeoutError:
                    # 타임아웃은 정상 (큐가 비어있을 때)
                    continue

                except Exception as e:
                    logger.error("STT 워커 처리 오류", exc_info=True, error=str(e))
                    segment_num = segment_num if 'segment_num' in locals() else -1
                    await self.result_queue.put({
                        "error": str(e),
                        "segment_number": segment_num
                    })
                    if 'segment_num' in locals():
                        self.metrics["pending_segments"] = max(0, self.metrics["pending_segments"] - 1)

        except Exception as e:
            logger.error("STT 워커 치명적 오류", exc_info=True, error=str(e))
        finally:
            logger.info("STT 워커 종료", job_id=self.job.job_id)

    async def process_audio_chunk(self, audio_chunk: bytes) -> AsyncGenerator[Dict[str, Any], None]:
        """
        오디오 청크 처리 및 실시간 결과 반환

        Args:
            audio_chunk: 오디오 바이트 청크

        Yields:
            {"type": "transcript_segment", "text": "...", "segment_number": N}
        """
        # VAD로 세그먼트 감지
        vad_start = time.perf_counter()
        segment_bytes = self.job.process_audio_chunk(audio_chunk)
        vad_duration = (time.perf_counter() - vad_start) * 1000
        self.metrics["total_vad_time"] += vad_duration

        if segment_bytes:
            self.segment_count += 1
            self.metrics["pending_segments"] += 1

            # ✅ STT 큐에 추가 (비동기로 처리됨)
            await self.processing_queue.put((segment_bytes, self.segment_count))

            logger.info(
                "세그먼트 감지, STT 큐 추가",
                segment_number=self.segment_count,
                vad_ms=round(vad_duration, 2),
                segment_bytes=len(segment_bytes),
                queue_size=self.metrics["pending_segments"]
            )

            # ✅ 결과 큐에서 완료된 결과 가져오기 (non-blocking)
            while not self.result_queue.empty():
                try:
                    result = await asyncio.wait_for(
                        self.result_queue.get(),
                        timeout=0.001  # 1ms
                    )

                    if "error" in result:
                        yield {
                            "type": "error",
                            "message": result["error"],
                            "segment_number": result["segment_number"]
                        }
                    else:
                        segment_text = result["text"]
                        if segment_text:
                            # Job의 문맥 업데이트
                            self.job.current_prompt_context += " " + segment_text
                            self.job.full_transcript.append(segment_text)

                            yield {
                                "type": "transcript_segment",
                                "text": segment_text,
                                "segment_number": result["segment_number"],
                                "processing_time_ms": round(result["duration_ms"], 2)
                            }

                except asyncio.TimeoutError:
                    break

    async def finalize(self) -> Dict[str, Any]:
        """
        스트림 종료 시 최종 처리
        """
        try:
            # ✅ 남은 버퍼 처리
            remaining_segment = self.job.vad_processor.flush()
            if remaining_segment:
                self.segment_count += 1
                self.metrics["pending_segments"] += 1
                await self.processing_queue.put((remaining_segment, self.segment_count))
                logger.info("남은 버퍼 처리", segment_number=self.segment_count)

            # ✅ 워커 종료 신호
            self.is_running = False
            await self.processing_queue.put(None)

            # ✅ 모든 STT 처리 완료 대기
            logger.info(
                "남은 세그먼트 처리 대기",
                pending_count=self.metrics["pending_segments"]
            )

            max_wait_time = 30.0  # 최대 30초 대기
            wait_start = time.perf_counter()

            while self.metrics["pending_segments"] > 0:
                # 타임아웃 체크
                if (time.perf_counter() - wait_start) > max_wait_time:
                    logger.warning(
                        "STT 처리 타임아웃",
                        remaining=self.metrics["pending_segments"]
                    )
                    break

                try:
                    result = await asyncio.wait_for(
                        self.result_queue.get(),
                        timeout=2.0
                    )

                    if "error" not in result and result.get("text"):
                        self.job.current_prompt_context += " " + result["text"]
                        self.job.full_transcript.append(result["text"])

                except asyncio.TimeoutError:
                    logger.warning("결과 대기 타임아웃")
                    continue

            # 워커 종료 대기
            if self.worker_task:
                try:
                    await asyncio.wait_for(self.worker_task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("워커 종료 타임아웃")

            # ThreadPoolExecutor 종료
            self.executor.shutdown(wait=False)

            final_transcript = self.job.get_full_transcript()

            # 성능 메트릭 출력
            avg_vad = self.metrics["total_vad_time"] / max(self.segment_count, 1)
            avg_stt = self.metrics["total_stt_time"] / max(self.metrics["total_segments"], 1)

            logger.info(
                "스트림 성능 요약",
                job_id=self.job.job_id,
                total_segments=self.segment_count,
                processed_segments=self.metrics["total_segments"],
                total_vad_ms=round(self.metrics["total_vad_time"], 2),
                total_stt_ms=round(self.metrics["total_stt_time"], 2),
                avg_vad_ms=round(avg_vad, 2),
                avg_stt_ms=round(avg_stt, 2)
            )

            if not final_transcript:
                logger.warning("대화 내용 없음", job_id=self.job.job_id)
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

            # STT 완료
            job_manager.update_status(
                self.job.job_id,
                JobStatus.TRANSCRIBED,
                transcript=final_transcript
            )

            logger.info("STT 완료", job_id=self.job.job_id, segment_count=self.segment_count)

            # 요약 시작
            summary_start = time.perf_counter()
            logger.info("요약 시작", job_id=self.job.job_id)
            summary_dict = await llm_service.get_summary(final_transcript)
            summary_duration = (time.perf_counter() - summary_start) * 1000

            job_manager.update_status(
                self.job.job_id,
                JobStatus.COMPLETED,
                summary=summary_dict
            )

            logger.info(
                "요약 완료",
                job_id=self.job.job_id,
                summary_ms=round(summary_duration, 2)
            )

            return {
                "type": "final_summary",
                "summary": summary_dict,
                "total_segments": self.segment_count
            }

        except Exception as e:
            error_msg = f"최종 처리 오류: {str(e)}"
            logger.error("최종 처리 실패", exc_info=True, job_id=self.job.job_id, error=str(e))
            job_manager.log_error(self.job.job_id, "stream_finalize", error_msg)
            return {
                "type": "error",
                "message": error_msg
            }