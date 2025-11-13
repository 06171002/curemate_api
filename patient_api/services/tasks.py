

import os
import sys
import asyncio  # <--- 1. asyncio를 임포트합니다.
from patient_api.repositories import job_repository
from patient_api.services import ollama_service, stt_service
from patient_api.core.celery_config import celery_app

# 2. (이름 변경) 기존 async 함수를 내부용(private) 함수로 변경합니다. (예: 맨 앞에 _ 추가)
async def _run_pipeline_async(job_id: str, audio_file_path: str):
    """
    (F-API-01이 호출하는) 백그라운드 작업의 메인 파이프라인.
    ... (이 함수 내부의 모든 코드는 100% 동일합니다) ...
    """

    print(f"[Worker] 🔵 작업 시작 (Job ID: {job_id}, File: {audio_file_path})")

    full_transcript = None

    try:
        # --- 1. 상태 변경: processing ---
        job_repository.update_job(job_id, {"status": "processing"})

        # --- 2. STT 실행 ---
        print(f"[Worker] (Job {job_id}) STT 작업을 시작합니다...")
        stt_generator = stt_service.transcribe_audio_streaming(audio_file_path)
        print(f"[Worker] (Job {job_id}) STT 작업 완료.")

        for segment_or_full in stt_generator:
            # 마지막 yield(full_transcript) 전까지는 segment_text
            # (이 방식은 마지막 yield를 구분해야 하므로, stt_service 수정이 필요)

            # (★수정 - 더 간단한 방식)
            # stt_service.transcribe_audio_streaming이
            # (1) segment를 yield하고, (2) 마지막에 full_text를 return 하도록 수정

            # (임시 수정 - stt_service.py의 yield가 2번 이상 실행된다고 가정)

            # (stt_service.py를 수정하지 않고 진행하는 방식)
            # transcribe_audio_streaming의 마지막 yield 값은 항상 "전체 텍스트"임.

            # (stt_service.py 수정이 필요합니다.
            #  transcribe_audio_streaming이 (segment, full_text) 튜플을 yield하거나
            #  transcribe_audio가 콜백 함수를 받도록 수정해야 합니다.)

            # --- (가장 간단한 수정안으로 다시 설계) ---
            # _run_pipeline_async 함수를 수정합니다.

            # (★수정) STT 실행 (제너레이터 사용)
            transcript_segments = []
            for segment in stt_service.transcribe_audio_streaming(audio_file_path):
                transcript_segments.append(segment)

                # (★핵심) 세그먼트를 Redis Pub/Sub으로 발행
                message_data = {
                    "type": "transcript_segment",
                    "text": segment
                }
                job_repository.publish_message(job_id, message_data)

            full_transcript = " ".join(transcript_segments)

            # --- 3. (DB 저장) STT 완료 상태를 DB에 저장 ---
            stt_result_data = {
                "status": "transcribed",
                "original_transcript": full_transcript
            }
            job_repository.update_job(job_id, stt_result_data)

            # --- 4. 요약 실행 ---
            print(f"[Worker] (Job {job_id}) Ollama 요약 작업을 시작합니다...")
            summary_dict = await ollama_service.get_summary(full_transcript)

            # (★핵심) 요약 결과를 Pub/Sub으로 발행
            summary_message = {
                "type": "final_summary",
                "summary": summary_dict
            }
            job_repository.publish_message(job_id, summary_message)

            # --- 5. (DB 저장) 최종 상태를 DB에 저장 ---
            final_result_data = {
                "status": "completed",
                "structured_summary": summary_dict
            }
            job_repository.update_job(job_id, final_result_data)

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
        job_repository.update_job(job_id, error_data)

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