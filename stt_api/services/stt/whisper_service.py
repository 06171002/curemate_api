from faster_whisper import WhisperModel
from typing import Optional, Generator
import numpy as np

from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger, LogContext
from stt_api.core.exceptions import (
    ModelNotLoadedError,
    STTProcessingError,
    AudioFormatError
)

# ✅ 로거 인스턴스 생성
logger = get_logger(__name__)

# 전역 모델 변수
_model: Optional[WhisperModel] = None


# ==================== 모델 로드 ====================

def load_stt_model() -> None:
    """
    FastAPI 서버 시작 시 STT 모델을 전역 변수에 로드

    Raises:
        STTProcessingError: 모델 로드 실패 시
    """
    global _model

    if _model is not None:
        logger.info("STT 모델이 이미 로드되었습니다")
        return

    logger.info(
        "STT 모델 로드 시작",
        model_size=settings.STT_MODEL_SIZE,
        device=settings.STT_DEVICE_TYPE,
        compute_type="default"
    )

    try:
        _model = WhisperModel(
            settings.STT_MODEL_SIZE,
            device=settings.STT_DEVICE_TYPE,
            compute_type="default"
        )

        logger.info(
            "STT 모델 로드 완료",
            model_size=settings.STT_MODEL_SIZE,
            device=settings.STT_DEVICE_TYPE
        )

    except Exception as e:
        logger.critical(
            "STT 모델 로드 실패",
            exc_info=True,
            model_size=settings.STT_MODEL_SIZE,
            device=settings.STT_DEVICE_TYPE,
            error=str(e)
        )

        raise STTProcessingError(
            file_path="N/A",
            reason=f"모델 로드 실패: {str(e)}"
        )


def _ensure_model_loaded() -> None:
    """
    모델이 로드되지 않았으면 로드 시도

    Raises:
        ModelNotLoadedError: 모델 로드 실패 시
    """
    global _model

    if _model is None:
        logger.warning("모델이 로드되지 않아 Lazy Loading 시도")

        try:
            load_stt_model()
        except Exception:
            raise ModelNotLoadedError()

    if _model is None:
        raise ModelNotLoadedError()


# ==================== STT 처리 함수들 ====================

def transcribe_audio(file_path: str) -> str:
    """
    오디오 파일을 텍스트로 변환 (전체 파일)

    Args:
        file_path: 오디오 파일 경로

    Returns:
        변환된 텍스트

    Raises:
        ModelNotLoadedError: 모델이 로드되지 않았을 때
        STTProcessingError: STT 처리 실패 시
    """
    _ensure_model_loaded()

    logger.info("STT 작업 시작", file_path=file_path)

    try:
        # VAD 필터 적용하여 음성 구간만 처리
        segments, info = _model.transcribe(
            file_path,
            language=settings.STT_LANGUAGE,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}
        )

        # 세그먼트 수집
        transcript_parts = []
        segment_count = 0

        for segment in segments:
            text = segment.text.strip()
            if text:
                transcript_parts.append(text)
                segment_count += 1

        full_transcript = " ".join(transcript_parts)

        logger.info(
            "STT 작업 완료",
            file_path=file_path,
            segment_count=segment_count,
            transcript_length=len(full_transcript),
            detected_language=info.language,
            language_probability=round(info.language_probability, 2)
        )

        return full_transcript

    except Exception as e:
        logger.error(
            "STT 작업 실패",
            exc_info=True,
            file_path=file_path,
            error=str(e)
        )

        raise STTProcessingError(
            file_path=file_path,
            reason=str(e)
        )


def transcribe_audio_streaming(file_path: str) -> Generator[str, None, None]:
    """
    오디오 파일을 스트리밍 방식으로 변환 (세그먼트별 yield)

    Args:
        file_path: 오디오 파일 경로

    Yields:
        감지된 각 세그먼트 텍스트

    Raises:
        ModelNotLoadedError: 모델이 로드되지 않았을 때
        STTProcessingError: STT 처리 실패 시
    """
    _ensure_model_loaded()

    logger.info("STT 스트리밍 작업 시작", file_path=file_path)

    try:
        segments, info = _model.transcribe(
            file_path,
            language=settings.STT_LANGUAGE,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}
        )

        segment_count = 0

        for segment in segments:
            segment_text = segment.text.strip()

            if segment_text:
                segment_count += 1

                logger.debug(
                    "STT 세그먼트 감지",
                    file_path=file_path,
                    segment_number=segment_count,
                    text_preview=segment_text[:50],
                    start_time=round(segment.start, 2),
                    end_time=round(segment.end, 2)
                )

                yield segment_text

        logger.info(
            "STT 스트리밍 작업 완료",
            file_path=file_path,
            total_segments=segment_count,
            detected_language=info.language
        )

    except Exception as e:
        logger.error(
            "STT 스트리밍 작업 실패",
            exc_info=True,
            file_path=file_path,
            error=str(e)
        )

        raise STTProcessingError(
            file_path=file_path,
            reason=str(e)
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

    Raises:
        ModelNotLoadedError: 모델이 로드되지 않았을 때
        AudioFormatError: 오디오 형식이 올바르지 않을 때
        STTProcessingError: STT 처리 실패 시
    """
    _ensure_model_loaded()

    logger.debug(
        "세그먼트 STT 시작",
        audio_size_bytes=len(audio_bytes),
        has_context=bool(initial_prompt)
    )

    try:
        # 1. 바이트를 int16 Numpy 배열로 변환
        try:
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
        except Exception as e:
            raise AudioFormatError(
                expected="16-bit PCM",
                actual=f"변환 실패: {str(e)}"
            )

        # 2. float32로 정규화
        audio_float32 = audio_np.astype(np.float32) / 32768.0

        # 3. STT 실행 (문맥 전달)
        segments, info = _model.transcribe(
            audio_float32,
            language=settings.STT_LANGUAGE,
            vad_filter=False,  # VAD는 이미 적용됨
            initial_prompt=initial_prompt
        )

        # 4. 결과 수집
        segment_texts = [seg.text.strip() for seg in segments]
        result_text = " ".join(segment_texts)

        logger.debug(
            "세그먼트 STT 완료",
            audio_size_bytes=len(audio_bytes),
            result_length=len(result_text),
            text_preview=result_text[:50]
        )

        return result_text

    except AudioFormatError:
        raise

    except Exception as e:
        logger.error(
            "세그먼트 STT 실패",
            exc_info=True,
            audio_size_bytes=len(audio_bytes),
            error=str(e)
        )

        raise STTProcessingError(
            file_path="[memory_bytes]",
            reason=str(e)
        )