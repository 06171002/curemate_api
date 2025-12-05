import asyncio
import time
from typing import AsyncGenerator, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from stt_api.services.stt import transcribe_segment_from_bytes
from stt_api.services.llm import llm_service
from stt_api.services.storage import job_manager, JobStatus
from stt_api.domain.streaming_job import StreamingJob
from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


class StreamPipeline:
    """
    실시간 스트리밍 파이프라인 (병렬 처리)

    ✅ faster-whisper와 WhisperLiveKit 모두 지원
    """

    def __init__(self, job: StreamingJob, max_workers: int = 3):
        self.job = job
        self.segment_count = 0
        self.use_vad = self.job.use_vad

        # ✅ mode 확인
        mode = job.metadata.get("mode")

        if mode == "google":
            # Google STT 사용
            from stt_api.services.stt.stt_factory import get_stt_service
            self.stt_service = get_stt_service(mode="google")
            self.stt_engine = "google"
            logger.info("StreamPipeline: Google STT 사용")
        else:
            # 기존 로직
            from stt_api.services.stt import stt_service
            self.stt_service = stt_service
            self.stt_engine = settings.STT_ENGINE

        # ✅ STT 병렬 처리를 위한 ThreadPoolExecutor
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

        # ✅ 처리 중인 세그먼트 큐
        self.processing_queue = asyncio.Queue()
        self.result_queue = asyncio.Queue()

        # ✅ 성능 메트릭 추적
        self.metrics = {
            "total_vad_time": 0.0,
            "total_stt_time": 0.0,
            "total_segments": 0,
            "pending_segments": 0
        }

        # ✅ 백그라운드 STT 워커 시작
        self.worker_task = None
        self.is_running = True

        logger.info(
            "StreamPipeline 초기화",
            job_id=job.job_id,
            stt_engine=self.stt_engine,
            use_vad=self.use_vad,
            max_workers=max_workers
        )

    async def start(self):
        """파이프라인 시작"""
        self.worker_task = asyncio.create_task(self._stt_worker())
        logger.info("STT 워커 시작", job_id=self.job.job_id, stt_engine=self.stt_engine)

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

                    segment_bytes, segment_num, segment_timestamp = segment_data

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
                        "duration_ms": stt_duration,
                        "absolute_timestamp": segment_timestamp,  # ✅ 절대 시간
                        "relative_time_sec": self.job.get_relative_time(segment_timestamp)  # ✅ 상대 시간
                    })
                    logger.debug(
                        "STT 워커 처리 완료",
                        segment_number=segment_num,
                        stt_ms=round(stt_duration, 2),
                        text_preview=segment_text[:30] if segment_text else "",
                        stt_engine=self.stt_engine
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
        # ✅ VAD/버퍼로 세그먼트 감지
        processing_start = time.perf_counter()
        # ✅ process_audio_chunk가 이제 (bytes, timestamp) 튜플 반환
        result = self.job.process_audio_chunk(audio_chunk)
        processing_duration = (time.perf_counter() - processing_start) * 1000

        # 메트릭에 VAD 시간 기록 (VAD 사용 시) 또는 버퍼 처리 시간 기록
        if self.use_vad:
            self.metrics["total_vad_time"] += processing_duration

        if result:
            segment_bytes, segment_timestamp = result
            self.segment_count += 1
            self.metrics["pending_segments"] += 1

            # ✅ 타임스탬프 포함하여 큐에 추가
            await self.processing_queue.put((
                segment_bytes,
                self.segment_count,
                segment_timestamp  # ✅ 타임스탬프 전달
            ))

            logger.info(
                "세그먼트 감지, STT 큐 추가",
                segment_number=self.segment_count,
                processing_ms=round(processing_duration, 2),
                segment_bytes=len(segment_bytes),
                queue_size=self.metrics["pending_segments"],
                stt_engine=self.stt_engine,
                detection_method="VAD" if self.use_vad else "Buffer"
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

                        await job_manager.save_segment(
                            job_id=self.job.job_id,
                            segment_text=segment_text,
                            start_time=result.get("relative_time_sec"),  # 상대 시간(초) 저장
                            end_time=None  # 종료 시간은 현재 로직에서 명시적이지 않으므로 None
                        )

                        # ✅ 타임스탬프 정보 포함하여 반환
                        yield {
                            "type": "transcript_segment",
                            "text": segment_text,
                            "segment_number": result["segment_number"],
                            "processing_time_ms": round(result["duration_ms"], 2),
                            "absolute_timestamp": result["absolute_timestamp"],  # ✅ 절대 시간
                            "relative_time_sec": round(result["relative_time_sec"], 3),  # ✅ 상대 시간
                            "stt_engine": self.stt_engine
                        }

            except asyncio.TimeoutError:
                break

    async def finalize(self) -> Dict[str, Any]:
        """
        스트림 종료 시 최종 처리

        ✅ 개선사항:
        - 워커가 완전히 종료될 때까지 대기하지 않음
        - STT 워커는 계속 실행하면서 결과만 수집
        - 타임아웃을 길게 설정 (MP3 전체 처리 고려)
        """
        try:
            # ✅ 남은 버퍼 처리
            remaining_segment = self.job.flush_buffer()
            if remaining_segment:
                segment_bytes, segment_timestamp = remaining_segment
                self.segment_count += 1
                self.metrics["pending_segments"] += 1
                await self.processing_queue.put((segment_bytes, self.segment_count, segment_timestamp))
                logger.info(
                    "남은 버퍼 처리",
                    segment_number=self.segment_count,
                    segment_bytes=len(segment_bytes)
                )

            # ✅ 워커 종료 신호 (큐 끝에 None 추가)
            # 주의: 워커는 계속 실행 중이므로 즉시 종료되지 않음
            logger.info(
                "워커 종료 신호 전송",
                pending_count=self.metrics["pending_segments"]
            )

            # ✅ 모든 STT 처리 완료 대기 (타임아웃 증가)
            max_wait_time = 180.0  # ✅ 3분으로 증가 (MP3 전체 처리 고려)
            wait_start = time.perf_counter()

            logger.info(
                "STT 처리 완료 대기 시작",
                pending_count=self.metrics["pending_segments"],
                max_wait_sec=max_wait_time
            )

            while self.metrics["pending_segments"] > 0:
                # 타임아웃 체크
                elapsed = time.perf_counter() - wait_start
                if elapsed > max_wait_time:
                    logger.warning(
                        "STT 처리 타임아웃",
                        remaining=self.metrics["pending_segments"],
                        elapsed_sec=round(elapsed, 2)
                    )
                    break

                try:
                    # ✅ 타임아웃을 5초로 증가
                    result = await asyncio.wait_for(
                        self.result_queue.get(),
                        timeout=5.0
                    )

                    if "error" not in result and result.get("text"):
                        text = result["tesx"]
                        self.job.current_prompt_context += " " + text
                        self.job.full_transcript.append(text)

                        # ✅ [추가됨] 종료 후 처리된 세그먼트도 DB에 저장
                        await job_manager.save_segment(
                            job_id=self.job.job_id,
                            segment_text=text,
                            start_time=result.get("relative_time_sec"),
                            end_time=None
                        )

                        logger.info(
                            "STT 결과 수신",
                            segment_number=result.get("segment_number"),
                            text_preview=result["text"][:50],
                            remaining=self.metrics["pending_segments"] - 1
                        )

                    self.metrics["pending_segments"] = max(0, self.metrics["pending_segments"] - 1)

                except asyncio.TimeoutError:
                    # ✅ 5초마다 진행 상황 로깅
                    logger.info(
                        "STT 처리 진행 중",
                        remaining=self.metrics["pending_segments"],
                        elapsed_sec=round(time.perf_counter() - wait_start, 2)
                    )
                    continue

            # ✅ 이제 워커 종료 신호 전송
            self.is_running = False
            await self.processing_queue.put(None)

            # 워커 종료 대기 (최대 10초)
            if self.worker_task:
                try:
                    await asyncio.wait_for(self.worker_task, timeout=10.0)
                    logger.info("STT 워커 정상 종료")
                except asyncio.TimeoutError:
                    logger.warning("워커 종료 타임아웃, 강제 종료")

            # ThreadPoolExecutor 종료
            self.executor.shutdown(wait=False)

            final_transcript = self.job.get_full_transcript()

            # 성능 메트릭 출력
            avg_processing = self.metrics["total_vad_time"] / max(self.segment_count, 1)
            avg_stt = self.metrics["total_stt_time"] / max(self.metrics["total_segments"], 1)

            logger.info(
                "스트림 성능 요약",
                job_id=self.job.job_id,
                stt_engine=self.stt_engine,
                total_segments=self.segment_count,
                processed_segments=self.metrics["total_segments"],
                total_processing_ms=round(self.metrics["total_vad_time"], 2),
                total_stt_ms=round(self.metrics["total_stt_time"], 2),
                avg_processing_ms=round(avg_processing, 2),
                avg_stt_ms=round(avg_stt, 2)
            )

            if not final_transcript:
                logger.warning("대화 내용 없음", job_id=self.job.job_id)
                await job_manager.update_status(
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
            await job_manager.update_status(
                self.job.job_id,
                JobStatus.TRANSCRIBED,
                transcript=final_transcript
            )

            logger.info(
                "STT 완료",
                job_id=self.job.job_id,
                segment_count=self.segment_count,
                stt_engine=self.stt_engine,
                transcript_length=len(final_transcript)
            )

            # 요약 시작
            summary_start = time.perf_counter()
            logger.info("요약 시작", job_id=self.job.job_id)

            # ✅ mode에 따라 LLM 선택
            mode = self.job.metadata.get("mode")

            if mode == "google":
                from stt_api.services.llm import get_llm_service
                llm = get_llm_service(mode="google")
                logger.info("StreamPipeline: Google Gemini 사용")
            else:
                from stt_api.services.llm import llm_service as llm

            summary_dict = await llm_service.get_summary(final_transcript)
            summary_duration = (time.perf_counter() - summary_start) * 1000

            await job_manager.update_status(
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
                "total_segments": self.segment_count,
                "stt_engine": self.stt_engine
            }

        except Exception as e:
            error_msg = f"최종 처리 오류: {str(e)}"
            logger.error(
                "최종 처리 실패",
                exc_info=True,
                job_id=self.job.job_id,
                error=str(e)
            )
            await job_manager.log_error(self.job.job_id, "stream_finalize", error_msg)
            return {
                "type": "error",
                "message": error_msg
            }