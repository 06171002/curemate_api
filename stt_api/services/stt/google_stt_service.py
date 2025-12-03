"""
Google Speech-to-Text 서비스
"""

from google.cloud import speech_v1
from google.cloud.speech_v1 import types
from typing import Optional, Generator
import io

from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import (
    ModelNotLoadedError,
    STTProcessingError,
    AudioFormatError
)

logger = get_logger(__name__)

# Google STT 클라이언트 (전역)
_client: Optional[speech_v1.SpeechClient] = None


def load_stt_model() -> None:
    """Google STT 클라이언트 초기화"""
    global _client

    if _client is not None:
        logger.info("Google STT 클라이언트가 이미 초기화되었습니다")
        return

    try:
        logger.info("Google STT 클라이언트 초기화 시작")
        _client = speech_v1.SpeechClient()
        logger.info("Google STT 클라이언트 초기화 완료")
    except Exception as e:
        logger.critical("Google STT 클라이언트 초기화 실패", exc_info=True, error=str(e))
        raise STTProcessingError(
            file_path="N/A",
            reason=f"Google STT 초기화 실패: {str(e)}"
        )


def _ensure_model_loaded() -> None:
    """클라이언트 확인"""
    global _client

    if _client is None:
        logger.warning("클라이언트가 초기화되지 않아 Lazy Loading 시도")
        try:
            load_stt_model()
        except Exception:
            raise ModelNotLoadedError()

    if _client is None:
        raise ModelNotLoadedError()


def transcribe_audio(file_path: str) -> str:
    """
    Google STT로 오디오 파일 전체 변환

    Args:
        file_path: 오디오 파일 경로

    Returns:
        변환된 텍스트
    """
    _ensure_model_loaded()

    logger.info("Google STT 작업 시작", file_path=file_path)

    try:
        # 오디오 파일 읽기
        with io.open(file_path, "rb") as audio_file:
            content = audio_file.read()

        # 오디오 설정
        audio = types.RecognitionAudio(content=content)

        config = types.RecognitionConfig(
            encoding=speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="ko-KR",
            enable_automatic_punctuation=True,
            model="default"
        )

        # STT 실행
        response = _client.recognize(config=config, audio=audio)

        # 결과 조합
        transcript_parts = []
        for result in response.results:
            transcript_parts.append(result.alternatives[0].transcript)

        full_transcript = " ".join(transcript_parts)

        logger.info(
            "Google STT 작업 완료",
            file_path=file_path,
            transcript_length=len(full_transcript)
        )

        return full_transcript

    except Exception as e:
        logger.error("Google STT 작업 실패", exc_info=True, file_path=file_path, error=str(e))
        raise STTProcessingError(file_path=file_path, reason=str(e))


def transcribe_audio_streaming(file_path: str) -> Generator[str, None, None]:
    """
    Google STT 스트리밍 변환 (Long Running Recognition)

    Args:
        file_path: 오디오 파일 경로

    Yields:
        감지된 세그먼트 텍스트
    """
    _ensure_model_loaded()

    logger.info("Google STT 스트리밍 작업 시작", file_path=file_path)

    try:
        # GCS URI를 사용하는 경우
        # audio = types.RecognitionAudio(uri=gcs_uri)

        # 로컬 파일 사용
        with io.open(file_path, "rb") as audio_file:
            content = audio_file.read()

        audio = types.RecognitionAudio(content=content)

        config = types.RecognitionConfig(
            encoding=speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="ko-KR",
            enable_automatic_punctuation=True,
        )

        # Long running operation
        operation = _client.long_running_recognize(config=config, audio=audio)

        logger.info("Google STT 작업 대기 중...")
        response = operation.result(timeout=300)

        segment_count = 0
        for result in response.results:
            segment_text = result.alternatives[0].transcript
            if segment_text:
                segment_count += 1
                logger.debug(
                    "Google STT 세그먼트",
                    segment_number=segment_count,
                    text_preview=segment_text[:50]
                )
                yield segment_text

        logger.info(
            "Google STT 스트리밍 작업 완료",
            file_path=file_path,
            total_segments=segment_count
        )

    except Exception as e:
        logger.error(
            "Google STT 스트리밍 작업 실패",
            exc_info=True,
            file_path=file_path,
            error=str(e)
        )
        raise STTProcessingError(file_path=file_path, reason=str(e))


def transcribe_segment_from_bytes(
        audio_bytes: bytes,
        initial_prompt: str = None
) -> str:
    """
    Google STT로 오디오 바이트 세그먼트 변환

    Args:
        audio_bytes: 오디오 바이트 데이터
        initial_prompt: 문맥 (Google STT는 지원하지 않음)

    Returns:
        변환된 텍스트
    """
    _ensure_model_loaded()

    logger.debug("Google STT 세그먼트 변환 시작", audio_size_bytes=len(audio_bytes))

    try:
        audio = types.RecognitionAudio(content=audio_bytes)

        config = types.RecognitionConfig(
            encoding=speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="ko-KR",
            enable_automatic_punctuation=True,
        )

        response = _client.recognize(config=config, audio=audio)

        transcript_parts = []
        for result in response.results:
            transcript_parts.append(result.alternatives[0].transcript)

        result_text = " ".join(transcript_parts)

        logger.info(
            "Google STT 세그먼트 변환 완료",
            audio_size_bytes=len(audio_bytes),
            result_length=len(result_text)
        )

        return result_text

    except Exception as e:
        logger.error(
            "Google STT 세그먼트 변환 실패",
            exc_info=True,
            audio_size_bytes=len(audio_bytes),
            error=str(e)
        )
        raise STTProcessingError(file_path="[memory_bytes]", reason=str(e))