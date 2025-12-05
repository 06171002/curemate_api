import asyncio
from stt_api.services.storage import job_manager, JobStatus
from stt_api.services.pipeline import run_batch_pipeline
from stt_api.core.celery_config import celery_app
from stt_api.core.logging_config import get_logger
from stt_api.services.llm import llm_service

logger = get_logger(__name__)


@celery_app.task
def run_stt_and_summary_pipeline(job_id: str, audio_file_path: str):
    """
    Celery 태스크: 배치 파이프라인 실행
    """
    try:
        # run_batch_pipeline 내부에서 대부분의 에러를 처리하지만,
        # asyncio.run 자체가 실패하는 경우를 대비해 외부 try-except 유지
        result = asyncio.run(run_batch_pipeline(job_id, audio_file_path))
        return result
    except Exception as e:
        error_msg = f"Asyncio 실행 실패: {str(e)}"
        logger.error("[Celery]", error_msg=error_msg)

        # ✅ 동기 컨텍스트(Celery Task)에서 비동기 메서드(JobManager)를 호출하기 위한 래퍼
        async def _handle_error():
            await job_manager.log_error(job_id, "celery_asyncio", error_msg)
            await job_manager.update_status(job_id, JobStatus.COMPLETED, error_message=error_msg)

        # ✅ 새로운 이벤트 루프를 생성하여 에러 처리 실행
        try:
            asyncio.run(_handle_error())
        except Exception as inner_e:
            logger.error("에러 핸들링 중 추가 오류 발생", error=str(inner_e))

        return {"status": "failed", "error": error_msg}


@celery_app.task(bind=True, max_retries=5)
def generate_room_summary_task(self, room_id: str):
    """
    Celery 백그라운드 Task: 방 통합 요약 생성

    Args:
        room_id: 방 ID
    """
    async def _generate_summary():
        try:
            logger.info("[RoomSummary] 통합 요약 Task 시작", room_id=room_id)

            # ✅ 안전장치 1: 다시 한번 모든 작업 완료 확인
            is_ready = await job_manager.is_room_ready_for_summary(room_id)

            if not is_ready:
                status_summary = await job_manager.get_room_job_status_summary(room_id)

                logger.warning(
                    "[RoomSummary] 아직 진행 중인 작업 있음, 10초 후 재시도",
                    room_id=room_id,
                    status_summary=status_summary,
                    retry_count=self.request.retries
                )

                # ✅ 10초 후 재시도 (최대 5회)
                raise self.retry(countdown=10, exc=Exception("작업 아직 진행 중"))

            # ✅ 안전장치 2: 완료된 모든 대화록 조회
            transcripts = await job_manager.get_completed_room_transcripts(room_id)

            if not transcripts:
                logger.warning(
                    "[RoomSummary] 요약할 대화 없음",
                    room_id=room_id
                )
                return {"status": "no_content", "room_id": room_id}

            # 참가자별 대화록 결합
            combined_text = ""
            for item in transcripts:
                member_id = item.get("member_id", "Unknown")
                transcript = item.get("transcript", "")
                reg_dttm = item.get("reg_dttm", "")

                combined_text += (
                    f"\n\n{'=' * 50}\n"
                    f"참가자: {member_id}\n"
                    f"시간: {reg_dttm}\n"
                    f"{'=' * 50}\n"
                    f"{transcript}"
                )

            logger.info(
                "[RoomSummary] 대화록 결합 완료",
                room_id=room_id,
                member_count=len(transcripts),
                total_length=len(combined_text)
            )

            # LLM으로 통합 요약 생성
            summary_result = await llm_service.get_summary(combined_text)
            # summary_result = await llm_service.get_medical_summary(combined_text)

            # DB에 저장
            success = await job_manager.update_room_summary(room_id, summary_result)

            if success:
                logger.info(
                    "[RoomSummary] 통합 요약 완료",
                    room_id=room_id,
                    member_count=len(transcripts),
                    summary_keys=list(summary_result.keys())
                )
                return {
                    "status": "completed",
                    "room_id": room_id,
                    "member_count": len(transcripts),
                    "summary": summary_result
                }
            else:
                raise Exception("DB 저장 실패")

        except Exception as e:
            error_msg = f"통합 요약 실패: {str(e)}"
            logger.error(
                "[RoomSummary] Task 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            raise

    # 비동기 함수 실행
    try:
        result = asyncio.run(_generate_summary())
        return result
    except Exception as e:
        logger.error(
            "[RoomSummary] Asyncio 실행 실패",
            room_id=room_id,
            error=str(e)
        )
        return {"status": "failed", "error": str(e)}