# worker.py (수정)

import os
import sys
import asyncio  # <--- 1. asyncio를 임포트합니다.
import job_manager
import stt_service
import ollama_service
from celery_config import celery_app

# 2. (이름 변경) 기존 async 함수를 내부용(private) 함수로 변경합니다. (예: 맨 앞에 _ 추가)
async def _run_pipeline_async(job_id: str, audio_file_path: str):
    """
    (F-API-01이 호출하는) 백그라운드 작업의 메인 파이프라인.
    ... (이 함수 내부의 모든 코드는 100% 동일합니다) ...
    """

    print(f"[Worker] 🔵 작업 시작 (Job ID: {job_id}, File: {audio_file_path})")

    try:
        # --- 1. 상태 변경: processing ---
        job_manager.update_job(job_id, {"status": "processing"})

        # --- 2. STT 실행 ---
        print(f"[Worker] (Job {job_id}) STT 작업을 시작합니다...")
        transcript_text = stt_service.transcribe_audio(audio_file_path)
        print(f"[Worker] (Job {job_id}) STT 작업 완료.")

        # --- 3. STT 결과 저장 및 상태 변경: transcribed ---
        stt_result_data = {
            "status": "transcribed",
            "original_transcript": transcript_text
        }
        job_manager.update_job(job_id, stt_result_data)

        # --- 4. 요약 실행 ---
        print(f"[Worker] (Job {job_id}) Ollama 요약 작업을 시작합니다...")
        summary_dict = await ollama_service.get_summary(transcript_text)
        print(f"[Worker] (Job {job_id}) Ollama 요약 작업 완료.")

        # --- 5. 요약 결과 저장 및 상태 변경: completed ---
        final_result_data = {
            "status": "completed",
            "structured_summary": summary_dict
        }
        job_manager.update_job(job_id, final_result_data)

        print(f"[Worker] 🟢 작업 성공 (Job ID: {job_id})")

    except Exception as e:
        # --- 6. (오류 발생 시) 상태를 'failed'로 변경 ---
        print(f"[Worker] 🔴 작업 실패 (Job ID: {job_id}): {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()

        error_data = {
            "status": "failed",
            "error_message": str(e)
        }
        job_manager.update_job(job_id, error_data)

    finally:
        # --- 7. (항상) 임시 파일 삭제 ---
        if os.path.exists(audio_file_path):
            try:
                os.remove(audio_file_path)
                print(f"[Worker] (Job {job_id}) 임시 파일 삭제 완료: {audio_file_path}")
            except Exception as e:
                print(f"[Worker] ⚠️ (Job {job_id}) 임시 파일 삭제 실패: {e}", file=sys.stderr)


# 3. (신규) Celery Task를 '동기식' 함수로 만듭니다.
@celery_app.task
def run_stt_and_summary_pipeline(job_id: str, audio_file_path: str):
    """
    이것은 Celery가 호출할 '동기식' 래퍼(Wrapper) 함수입니다.
    이 함수의 유일한 역할은 '비동기' 파이프라인을
    asyncio.run()을 통해 실행하고, 끝날 때까지 기다리는 것입니다.
    """
    asyncio.run(_run_pipeline_async(job_id, audio_file_path))