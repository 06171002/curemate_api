import os
import sys
import job_manager  # (DB 관리자)
import stt_service  # (STT 전문가)
import ollama_service  # (요약 전문가)
from celery_config import celery_app


# (참고) ollama_service.get_summary가 async 함수이므로,
# 이 총괄 함수도 async def로 선언하는 것이 좋습니다.
@celery_app.task
async def run_stt_and_summary_pipeline(job_id: str, audio_file_path: str):
    """
    (F-API-01이 호출하는) 백그라운드 작업의 메인 파이프라인.

    1. 상태를 'processing'으로 변경
    2. STT 실행 (stt_service)
    3. 상태를 'transcribed'로 변경 + 결과 저장 (job_manager)
    4. 요약 실행 (ollama_service)
    5. 상태를 'completed'로 변경 + 결과 저장 (job_manager)
    6. (오류 시) 상태를 'failed'로 변경
    7. (항상) 임시 파일 삭제
    """

    print(f"[Worker] 🔵 작업 시작 (Job ID: {job_id}, File: {audio_file_path})")

    try:
        # --- 1. 상태 변경: processing ---
        job_manager.update_job(job_id, {"status": "processing"})

        # --- 2. STT 실행 ---
        # stt_service.transcribe는 CPU/GPU를 많이 쓰는 작업이므로
        # (I/O bound가 아니므로) 'await' 없이 동기적으로 실행합니다.
        print(f"[Worker] (Job {job_id}) STT 작업을 시작합니다...")
        transcript_text = stt_service.transcribe_audio(audio_file_path)
        print(f"[Worker] (Job {job_id}) STT 작업 완료.")

        # --- 3. STT 결과 저장 및 상태 변경: transcribed ---
        stt_result_data = {
            "status": "transcribed",
            "original_transcript": transcript_text
        }
        job_manager.update_job(job_id, stt_result_data)
        # (이 시점부터 클라이언트는 Polling 시 STT 결과를 볼 수 있습니다!)

        # --- 4. 요약 실행 ---
        # ollama_service.get_summary는 I/O bound(네트워크) 작업이므로
        # 'await'로 비동기 실행합니다.
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
        # (traceback도 로그에 남기면 디버깅에 좋습니다)
        import traceback
        traceback.print_exc()

        error_data = {
            "status": "failed",
            "error_message": str(e)  # 오류 메시지를 DB에 저장
        }
        job_manager.update_job(job_id, error_data)

    finally:
        # --- 7. (항상) 임시 파일 삭제 ---
        # 작업이 성공하든 실패하든, 서버에 쌓이는 임시 파일을 삭제합니다.
        if os.path.exists(audio_file_path):
            try:
                os.remove(audio_file_path)
                print(f"[Worker] (Job {job_id}) 임시 파일 삭제 완료: {audio_file_path}")
            except Exception as e:
                # (파일 삭제 실패는 Job 상태를 바꾸진 않습니다)
                print(f"[Worker] ⚠️ (Job {job_id}) 임시 파일 삭제 실패: {e}", file=sys.stderr)