"""
WhisperLiveKit 전용 WebSocket 엔드포인트 (버그 수정)

FrontData 객체 처리 추가
"""

import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from whisperlivekit import TranscriptionEngine, AudioProcessor

from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger
from stt_api.services.llm import llm_service
from stt_api.services.storage import job_manager, JobType, JobStatus

logger = get_logger(__name__)

router = APIRouter()

# ✅ 전역 WhisperLiveKit 엔진 (한 번만 로드)
_whisperlive_engine = None


async def get_whisperlive_engine():
    """전역 WhisperLiveKit 엔진 가져오기 (Lazy Loading)"""
    global _whisperlive_engine

    if _whisperlive_engine is None:
        logger.info(
            "WhisperLiveKit 엔진 로드 시작",
            model_size=settings.STT_MODEL_SIZE,
            language=settings.STT_LANGUAGE
        )

        _whisperlive_engine = TranscriptionEngine(
            model=settings.STT_MODEL_SIZE,
            language=settings.STT_LANGUAGE,
            diarization=settings.WHISPERLIVE_USE_DIARIZATION,
            backend="faster_whisper",
            device=settings.STT_DEVICE_TYPE
        )

        logger.info("WhisperLiveKit 엔진 로드 완료")

    return _whisperlive_engine


@router.post("/api/v1/stream/create", status_code=201)
def create_stream_job():
    """스트림 작업 생성"""
    import uuid

    job_id = str(uuid.uuid4())
    metadata = {"stt_engine": "whisperlivekit"}

    if not job_manager.create_job(job_id, JobType.REALTIME, metadata=metadata):
        raise HTTPException(status_code=500, detail="작업 생성 실패")

    logger.info("새 스트림 작업 생성됨", job_id=job_id)
    return {
        "job_id": job_id,
        "job_type": "REALTIME",
        "status": "pending"
    }


# ✅ FrontData/TranscriptionResult 안전하게 처리하는 헬퍼 함수
def extract_text_from_result(result) -> str:
    """
    WhisperLiveKit result 객체에서 텍스트 추출

    환경에 따라 다른 객체 타입 반환:
    - TranscriptionResult: .text 속성
    - FrontData: .lines (리스트) 또는 .buffer_transcription
    """
    # 1. text 속성 확인 (TranscriptionResult)
    if hasattr(result, 'text'):
        text = result.text
        if isinstance(text, str) and text.strip():
            return text.strip()

    # 2. lines 속성 확인 (FrontData) ⭐ 핵심!
    if hasattr(result, 'lines'):
        lines = result.lines
        if isinstance(lines, list) and len(lines) > 0:
            # lines는 딕셔너리 리스트일 수 있음
            texts = []
            for line in lines:
                if isinstance(line, dict):
                    # {'text': '...', 'start': 0.0, 'end': 1.0} 형태
                    if 'text' in line:
                        texts.append(line['text'])
                elif isinstance(line, str):
                    texts.append(line)

            if texts:
                return " ".join(texts).strip()

    # 3. buffer_transcription 확인 (FrontData 임시 버퍼)
    if hasattr(result, 'buffer_transcription'):
        buffer = result.buffer_transcription
        if isinstance(buffer, str) and buffer.strip():
            return buffer.strip()
        elif isinstance(buffer, list) and len(buffer) > 0:
            return " ".join([str(b) for b in buffer]).strip()

    # 4. content 속성 확인
    if hasattr(result, 'content'):
        content = result.content
        if isinstance(content, str):
            return content.strip()
        elif isinstance(content, dict) and 'text' in content:
            return content['text'].strip()

    # 5. 디버깅: 실제 값 출력
    if hasattr(result, '__dict__'):
        result_dict = result.__dict__

        # ✅ 디버깅 로그 (한 번만 출력)
        if not hasattr(extract_text_from_result, '_debug_printed'):
            logger.info(
                "FrontData 상세 구조 (첫 결과만)",
                lines=result_dict.get('lines'),
                buffer_transcription=result_dict.get('buffer_transcription'),
                status=result_dict.get('status'),
                error=result_dict.get('error')
            )
            extract_text_from_result._debug_printed = True

    # 6. 실패 (경고는 최소화)
    return ""


@router.websocket("/ws/v1/stream/{job_id}")
async def whisperlive_stream(websocket: WebSocket, job_id: str):
    """
    ✅ WhisperLiveKit 전용 WebSocket 엔드포인트

    클라이언트 → 오디오 스트림 → WhisperLiveKit → 실시간 텍스트 반환
    """

    # 1. 작업 확인
    job = job_manager.get_job(job_id)
    if not job:
        logger.error("존재하지 않는 Job ID", job_id=job_id)
        await websocket.close(code=1008, reason="Job ID not found")
        return

    # 2. WebSocket 연결 수락
    await websocket.accept()
    logger.info("클라이언트 연결됨", job_id=job_id)

    job_manager.update_status(job_id, JobStatus.PROCESSING)

    await websocket.send_json({
        "type": "connection_success",
        "message": f"Job {job_id}에 연결되었습니다."
    })

    # 3. WhisperLiveKit 엔진 가져오기
    try:
        engine = await get_whisperlive_engine()
    except Exception as e:
        error_msg = f"엔진 로드 실패: {str(e)}"
        logger.error("엔진 로드 실패", exc_info=True, error=str(e))
        await websocket.send_json({"type": "error", "message": error_msg})
        await websocket.close(code=1011, reason=error_msg)
        return

    # 4. AudioProcessor 생성 (이 연결 전용)
    audio_processor = AudioProcessor(transcription_engine=engine)
    result_generator = await audio_processor.create_tasks()

    # 5. 대화록 수집
    transcript_segments = []
    segment_count = 0

    # ✅ 백그라운드: 결과 수집 & WebSocket 전송 (수정됨)
    async def collect_and_send_results():
        nonlocal segment_count
        try:
            async for result in result_generator:
                # ✅ 안전한 텍스트 추출
                text = extract_text_from_result(result)

                if not text:
                    continue

                segment_count += 1
                transcript_segments.append(text)

                logger.info(
                    "세그먼트 감지",
                    job_id=job_id,
                    segment_number=segment_count,
                    text_preview=text[:50]
                )

                # ✅ 클라이언트에게 즉시 전송
                try:
                    await websocket.send_json({
                        "type": "transcript_segment",
                        "text": text,
                        "segment_number": segment_count
                    })
                except Exception as send_error:
                    logger.warning("전송 실패", error=str(send_error))
                    break

        except asyncio.CancelledError:
            logger.info("결과 수집 종료", job_id=job_id)
        except Exception as e:
            logger.error("결과 수집 오류", exc_info=True, error=str(e))

    # 6. 백그라운드 태스크 시작
    result_task = asyncio.create_task(collect_and_send_results())

    try:
        # ✅ 메인 루프: 클라이언트로부터 오디오 수신
        logger.info("오디오 스트림 수신 시작", job_id=job_id)

        chunk_count = 0
        while True:
            # 오디오 청크 수신
            audio_chunk = await websocket.receive_bytes()
            chunk_count += 1

            # ✅ 에러 핸들링 추가
            try:
                # WhisperLiveKit에 전달
                await audio_processor.process_audio(audio_chunk)
            except Exception as process_error:
                logger.warning(
                    "오디오 처리 오류",
                    chunk_number=chunk_count,
                    error=str(process_error)
                )
                # 계속 진행 (치명적 오류 아님)

    except WebSocketDisconnect:
        logger.info("클라이언트 연결 끊김", job_id=job_id, chunks_received=chunk_count)

    except Exception as e:
        error_msg = f"스트리밍 오류: {str(e)}"
        logger.error("스트리밍 오류", exc_info=True, error=str(e))
        job_manager.log_error(job_id, "whisperlive_stream", error_msg)

    finally:
        # 7. 정리 작업
        logger.info("최종 처리 시작", job_id=job_id)

        # ✅ 결과 수집 태스크 종료 대기 (최대 10초)
        try:
            await asyncio.wait_for(result_task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("결과 수집 타임아웃", job_id=job_id)
            result_task.cancel()
            try:
                await result_task
            except asyncio.CancelledError:
                pass

        # 8. 전체 대화록 생성
        full_transcript = " ".join(transcript_segments)

        if not full_transcript:
            logger.warning("대화 내용 없음", job_id=job_id)
            job_manager.update_status(
                job_id,
                JobStatus.TRANSCRIBED,
                transcript="",
                error_message="대화 내용 없음"
            )

            try:
                await websocket.send_json({
                    "type": "error",
                    "message": "대화 내용이 없습니다"
                })
            except:
                pass

            return

        # STT 완료
        job_manager.update_status(
            job_id,
            JobStatus.TRANSCRIBED,
            transcript=full_transcript
        )

        logger.info(
            "STT 완료",
            job_id=job_id,
            segment_count=segment_count,
            transcript_length=len(full_transcript)
        )

        # 9. 요약 생성
        try:
            logger.info("요약 시작", job_id=job_id)
            summary_dict = await llm_service.get_summary(full_transcript)

            job_manager.update_status(
                job_id,
                JobStatus.COMPLETED,
                summary=summary_dict
            )

            logger.info("요약 완료", job_id=job_id)

            # 최종 결과 전송
            try:
                await websocket.send_json({
                    "type": "final_summary",
                    "summary": summary_dict,
                    "total_segments": segment_count
                })
            except:
                pass  # 연결이 끊긴 경우 무시

        except Exception as e:
            error_msg = f"요약 실패: {str(e)}"
            logger.error("요약 실패", exc_info=True, error=str(e))
            job_manager.log_error(job_id, "summary", error_msg)

        logger.info("작업 완료", job_id=job_id)