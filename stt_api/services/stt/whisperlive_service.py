"""
WhisperLiveKit 기반 STT 서비스

기존 faster-whisper와 동일한 인터페이스를 제공하면서
WhisperLiveKit의 고성능 스트리밍 기능을 활용합니다.
"""

import asyncio
from typing import Optional, Generator
import numpy as np
from whisperlivekit import TranscriptionEngine, AudioProcessor
from io import BytesIO

from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import (
    ModelNotLoadedError,
    STTProcessingError,
    AudioFormatError
)

logger = get_logger(__name__)

# 전역 엔진 변수
_engine: Optional[TranscriptionEngine] = None
_audio_processor: Optional[AudioProcessor] = None


# ==================== 모델 로드 ====================

def load_stt_model() -> None:
    """WhisperLiveKit 엔진 초기화"""
    global _engine, _audio_processor

    if _engine is not None:
        logger.info("WhisperLiveKit 엔진이 이미 로드되었습니다")
        return

    logger.info(
        "WhisperLiveKit 엔진 로드 시작",
        model_size=settings.STT_MODEL_SIZE,
        language=settings.STT_LANGUAGE
    )

    try:
        _engine = TranscriptionEngine(
            model=settings.STT_MODEL_SIZE,
            language=settings.STT_LANGUAGE,
            diarization=settings.WHISPERLIVE_USE_DIARIZATION,
            backend="faster_whisper",
            device=settings.STT_DEVICE_TYPE
        )

        _audio_processor = AudioProcessor(transcription_engine=_engine)

        logger.info(
            "WhisperLiveKit 엔진 로드 완료",
            model_size=settings.STT_MODEL_SIZE
        )

    except Exception as e:
        logger.critical(
            "WhisperLiveKit 엔진 로드 실패",
            exc_info=True,
            error=str(e)
        )
        raise STTProcessingError(
            file_path="N/A",
            reason=f"WhisperLiveKit 로드 실패: {str(e)}"
        )


def _ensure_model_loaded() -> None:
    """엔진이 로드되지 않았으면 로드 시도"""
    global _engine, _audio_processor

    if _engine is None or _audio_processor is None:
        logger.warning("엔진이 로드되지 않아 Lazy Loading 시도")
        try:
            load_stt_model()
        except Exception:
            raise ModelNotLoadedError()

    if _engine is None:
        raise ModelNotLoadedError()


# ==================== STT 처리 함수들 ====================

def transcribe_audio(file_path: str) -> str:
    """
    오디오 파일을 텍스트로 변환 (전체 파일)

    Args:
        file_path: 오디오 파일 경로

    Returns:
        변환된 텍스트
    """
    _ensure_model_loaded()

    logger.info("WhisperLiveKit STT 작업 시작", file_path=file_path)

    try:
        # 동기 함수를 비동기로 실행
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_transcribe_file_async(file_path))
        loop.close()

        return result

    except Exception as e:
        logger.error(
            "WhisperLiveKit STT 작업 실패",
            exc_info=True,
            file_path=file_path,
            error=str(e)
        )
        raise STTProcessingError(file_path=file_path, reason=str(e))


async def _transcribe_file_async(file_path: str) -> str:
    """비동기 파일 변환 (내부용)"""
    result_generator = await _audio_processor.create_tasks()
    transcript_parts = []

    # 결과 수집 태스크
    async def collect_results():
        try:
            async for res in result_generator:
                text = res.text.strip()
                if text:
                    transcript_parts.append(text)
        except Exception as e:
            logger.warning("결과 수집 중 오류", error=str(e))

    collector = asyncio.create_task(collect_results())

    # 파일 스트리밍
    CHUNK_SIZE = 4096
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            await _audio_processor.process_audio(chunk)
            await asyncio.sleep(0.02)  # 처리 시간 확보

    # 남은 결과 처리 대기
    await asyncio.sleep(2.0)
    collector.cancel()

    full_transcript = " ".join(transcript_parts)

    logger.info(
        "WhisperLiveKit STT 작업 완료",
        file_path=file_path,
        segment_count=len(transcript_parts),
        transcript_length=len(full_transcript)
    )

    return full_transcript


def transcribe_audio_streaming(file_path: str) -> Generator[str, None, None]:
    """
    오디오 파일을 스트리밍 방식으로 변환 (세그먼트별 yield)

    Args:
        file_path: 오디오 파일 경로

    Yields:
        감지된 각 세그먼트 텍스트
    """
    _ensure_model_loaded()

    logger.info("WhisperLiveKit 스트리밍 작업 시작", file_path=file_path)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 비동기 제너레이터를 동기 제너레이터로 변환
        async_gen = _transcribe_streaming_async(file_path)

        while True:
            try:
                segment = loop.run_until_complete(async_gen.__anext__())
                yield segment
            except StopAsyncIteration:
                break

        loop.close()

    except Exception as e:
        logger.error(
            "WhisperLiveKit 스트리밍 작업 실패",
            exc_info=True,
            file_path=file_path,
            error=str(e)
        )
        raise STTProcessingError(file_path=file_path, reason=str(e))


async def _transcribe_streaming_async(file_path: str):
    """비동기 스트리밍 변환 (내부용)"""
    result_generator = await _audio_processor.create_tasks()
    segment_count = 0

    # 결과를 yield하는 큐
    result_queue = asyncio.Queue()

    # 결과 수집 태스크
    async def collect_results():
        try:
            async for res in result_generator:
                text = res.text.strip()
                if text:
                    await result_queue.put(text)
        except Exception as e:
            logger.warning("결과 수집 중 오류", error=str(e))
        finally:
            await result_queue.put(None)  # 종료 신호

    collector = asyncio.create_task(collect_results())

    # 파일 스트리밍 태스크
    async def stream_file():
        CHUNK_SIZE = 4096
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                await _audio_processor.process_audio(chunk)
                await asyncio.sleep(0.02)

    streamer = asyncio.create_task(stream_file())

    # 결과를 실시간으로 yield
    while True:
        text = await result_queue.get()
        if text is None:  # 종료 신호
            break

        segment_count += 1
        logger.debug(
            "WhisperLiveKit 세그먼트 감지",
            segment_number=segment_count,
            text_preview=text[:50]
        )
        yield text

    # 정리
    await streamer
    collector.cancel()

    logger.info(
        "WhisperLiveKit 스트리밍 작업 완료",
        file_path=file_path,
        total_segments=segment_count
    )


def transcribe_segment_from_bytes(
        audio_bytes: bytes,
        initial_prompt: str = None
) -> str:
    """
    오디오 바이트 세그먼트를 텍스트로 변환 (실시간 스트리밍용)

    Args:
        audio_bytes: 16kHz, 16-bit PCM 오디오 데이터
        initial_prompt: STT 문맥 (이전 대화 내용)

    Returns:
        변환된 텍스트
    """
    _ensure_model_loaded()

    logger.debug(
        "WhisperLiveKit 세그먼트 STT 시작",
        audio_size_bytes=len(audio_bytes),
        has_context=bool(initial_prompt)
    )

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            _transcribe_segment_async(audio_bytes, initial_prompt)
        )
        loop.close()

        return result

    except Exception as e:
        logger.error(
            "WhisperLiveKit 세그먼트 STT 실패",
            exc_info=True,
            audio_size_bytes=len(audio_bytes),
            error=str(e)
        )
        raise STTProcessingError(
            file_path="[memory_bytes]",
            reason=str(e)
        )


async def _transcribe_segment_async(
        audio_bytes: bytes,
        initial_prompt: str = None
) -> str:
    """비동기 세그먼트 변환 (내부용)"""
    result_generator = await _audio_processor.create_tasks()
    transcript_parts = []

    # 결과 수집
    async def collect_results():
        try:
            async for res in result_generator:
                text = res.text.strip()
                if text:
                    transcript_parts.append(text)
        except Exception:
            pass

    collector = asyncio.create_task(collect_results())

    # 오디오 처리
    await _audio_processor.process_audio(audio_bytes)
    await asyncio.sleep(0.5)  # 처리 시간 확보

    collector.cancel()

    result_text = " ".join(transcript_parts)

    logger.info(
        "WhisperLiveKit 세그먼트 STT 완료",
        audio_size_bytes=len(audio_bytes),
        result_length=len(result_text),
        text_preview=result_text[:50]
    )

    return result_text