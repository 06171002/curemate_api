import asyncio
from stt_api.services.storage import job_manager, JobStatus
from stt_api.services.pipeline import run_batch_pipeline
from stt_api.core.celery_config import celery_app
from stt_api.core.logging_config import get_logger

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
            await job_manager.update_status(job_id, JobStatus.FAILED, error_message=error_msg)

        # ✅ 새로운 이벤트 루프를 생성하여 에러 처리 실행
        try:
            asyncio.run(_handle_error())
        except Exception as inner_e:
            logger.error("에러 핸들링 중 추가 오류 발생", error=str(inner_e))

        return {"status": "failed", "error": error_msg}