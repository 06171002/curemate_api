# patient_api/services/tasks.py

import asyncio
from patient_api.services.storage import job_manager, JobStatus
from patient_api.services.pipeline import run_batch_pipeline
from patient_api.core.celery_config import celery_app


@celery_app.task
def run_stt_and_summary_pipeline(job_id: str, audio_file_path: str):
    """
    Celery 태스크: 배치 파이프라인 실행
    """
    try:
        result = asyncio.run(run_batch_pipeline(job_id, audio_file_path))
        return result
    except Exception as e:
        error_msg = f"Asyncio 실행 실패: {str(e)}"
        print(f"[Celery] 🔴 {error_msg}")

        job_manager.log_error(job_id, "celery_asyncio", error_msg)
        job_manager.update_status(job_id, JobStatus.FAILED, error_message=error_msg)
        return {"status": "failed", "error": error_msg}