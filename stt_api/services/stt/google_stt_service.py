"""
Google Speech-to-Text 서비스 (파일 크기/길이별 자동 선택)
"""

import os
import io
import uuid
from google.cloud import speech_v1
from google.cloud import storage
from google.cloud.speech_v1 import types
from typing import Optional, Generator, Tuple
from pydub import AudioSegment

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
_storage_client: Optional[storage.Client] = None


# ==================== 초기화 ====================

def load_stt_model() -> None:
    """Google STT 클라이언트 초기화"""
    global _client, _storage_client

    if _client is not None:
        logger.info("Google STT 클라이언트가 이미 초기화되었습니다")
        return

    try:
        logger.info("Google STT 클라이언트 초기화 시작")
        _client = speech_v1.SpeechClient()
        _storage_client = storage.Client()
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


# ==================== GCS 업로드 ====================

def _upload_to_gcs(file_path: str, bucket_name: str) -> str:
    """
    오디오 파일을 GCS에 업로드하고 URI 반환

    Args:
        file_path: 로컬 파일 경로
        bucket_name: GCS 버킷 이름

    Returns:
        gs://bucket-name/file-name.mp3

    Raises:
        STTProcessingError: 업로드 실패 시
    """
    try:
        bucket = _storage_client.bucket(bucket_name)

        # 고유한 파일명 생성
        file_ext = os.path.splitext(file_path)[1]
        blob_name = f"stt-audio/{uuid.uuid4()}{file_ext}"

        blob = bucket.blob(blob_name)
        blob.upload_from_filename(file_path)

        gcs_uri = f"gs://{bucket_name}/{blob_name}"

        logger.info(
            "GCS 업로드 완료",
            local_path=file_path,
            gcs_uri=gcs_uri
        )

        return gcs_uri

    except Exception as e:
        logger.error("GCS 업로드 실패", exc_info=True, error=str(e))
        raise STTProcessingError(
            file_path=file_path,
            reason=f"GCS 업로드 실패: {str(e)}"
        )


def _delete_from_gcs(gcs_uri: str) -> None:
    """
    GCS에서 파일 삭제 (정리용)

    Args:
        gcs_uri: gs://bucket-name/file-name.mp3
    """
    try:
        # gs://bucket-name/path/to/file.mp3 -> bucket-name, path/to/file.mp3
        parts = gcs_uri.replace("gs://", "").split("/", 1)
        if len(parts) != 2:
            return

        bucket_name, blob_name = parts
        bucket = _storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.delete()

        logger.info("GCS 파일 삭제 완료", gcs_uri=gcs_uri)

    except Exception as e:
        logger.warning("GCS 파일 삭제 실패 (무시)", error=str(e))


# ==================== 파일 검증 ====================

def _get_audio_duration(file_path: str) -> float:
    """
    오디오 파일 길이(초) 반환

    Args:
        file_path: 오디오 파일 경로

    Returns:
        파일 길이(초)
    """
    try:
        audio = AudioSegment.from_file(file_path)
        duration_seconds = len(audio) / 1000.0

        logger.debug(
            "오디오 길이 측정",
            file_path=file_path,
            duration_sec=round(duration_seconds, 2)
        )

        return duration_seconds

    except Exception as e:
        logger.warning(
            "오디오 길이 측정 실패, 안전하게 비동기 API 사용",
            error=str(e)
        )
        # 측정 실패 시 안전하게 61초 반환 (비동기 API 사용하도록)
        return 61.0


def _should_use_sync_api(file_path: str) -> bool:
    """
    동기 API 사용 가능 여부 판단

    조건:
    1. 파일 크기 ≤ 10MB
    2. 파일 길이 ≤ 60초

    Args:
        file_path: 오디오 파일 경로

    Returns:
        True: 동기 API 사용 가능
        False: 비동기 API 필요
    """
    # 1. 파일 크기 체크
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

    if file_size_mb > 10:
        logger.info(
            f"파일 크기 초과 ({file_size_mb:.2f}MB > 10MB), 비동기 API 필요"
        )
        return False

    # 2. 파일 길이 체크
    duration_sec = _get_audio_duration(file_path)

    if duration_sec > 60:
        logger.info(
            f"파일 길이 초과 ({duration_sec:.1f}초 > 60초), 비동기 API 필요"
        )
        return False

    # 둘 다 통과
    logger.info(
        f"동기 API 사용 가능 "
        f"(크기: {file_size_mb:.2f}MB, 길이: {duration_sec:.1f}초)"
    )
    return True


# ==================== 오디오 설정 ====================

def _get_audio_config(file_path: str) -> Tuple[speech_v1.RecognitionConfig.AudioEncoding, int]:
    """
    파일 확장자를 기반으로 Google STT 인코딩 포맷과 샘플레이트를 결정
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".mp3":
        return speech_v1.RecognitionConfig.AudioEncoding.MP3, 44100
    elif ext == ".wav":
        return speech_v1.RecognitionConfig.AudioEncoding.LINEAR16, 16000
    elif ext == ".flac":
        return speech_v1.RecognitionConfig.AudioEncoding.FLAC, 16000
    elif ext == ".ogg":
        return speech_v1.RecognitionConfig.AudioEncoding.OGG_OPUS, 16000
    else:
        logger.warning(f"지원하지 않는 확장자: {ext}. MP3로 시도합니다.")
        return speech_v1.RecognitionConfig.AudioEncoding.MP3, 44100


# ==================== 동기 API (짧은 파일) ====================

def _transcribe_sync(file_path: str) -> Generator[str, None, None]:
    """
    동기 API 사용 (10MB 이하 파일)

    Args:
        file_path: 오디오 파일 경로

    Yields:
        세그먼트 텍스트
    """
    try:
        # 파일 읽기
        with io.open(file_path, "rb") as audio_file:
            content = audio_file.read()

        # 오디오 설정
        encoding_type, sample_rate = _get_audio_config(file_path)

        audio = types.RecognitionAudio(content=content)
        config = types.RecognitionConfig(
            encoding=encoding_type,
            sample_rate_hertz=sample_rate,
            language_code="ko-KR",
            enable_automatic_punctuation=True,
        )

        logger.info(
            "Google STT 동기 API 호출",
            file_path=file_path,
            encoding=encoding_type.name,
            sample_rate=sample_rate
        )

        # STT 실행
        response = _client.recognize(config=config, audio=audio)

        # 결과 수집
        segment_count = 0
        for result in response.results:
            segment_text = result.alternatives[0].transcript
            if segment_text:
                segment_count += 1
                logger.debug(
                    "Google STT 세그먼트 (동기)",
                    segment_number=segment_count,
                    text_preview=segment_text[:50]
                )
                yield segment_text

        logger.info(
            "Google STT 동기 작업 완료",
            file_path=file_path,
            total_segments=segment_count
        )

    except Exception as e:
        logger.error("Google STT 동기 작업 실패", exc_info=True, error=str(e))
        raise STTProcessingError(file_path=file_path, reason=str(e))


# ==================== 비동기 API (긴 파일) ====================

def _transcribe_long_running(file_path: str, gcs_bucket: str) -> Generator[str, None, None]:
    """
    비동기 API 사용 (10MB 이상 파일, GCS 필요)

    Args:
        file_path: 오디오 파일 경로
        gcs_bucket: GCS 버킷 이름

    Yields:
        세그먼트 텍스트
    """
    gcs_uri = None

    try:
        # 1. GCS에 업로드
        gcs_uri = _upload_to_gcs(file_path, gcs_bucket)

        # 2. 오디오 설정
        encoding_type, sample_rate = _get_audio_config(file_path)

        audio = types.RecognitionAudio(uri=gcs_uri)
        config = types.RecognitionConfig(
            encoding=encoding_type,
            sample_rate_hertz=sample_rate,
            language_code="ko-KR",
            enable_automatic_punctuation=True,
        )

        logger.info(
            "Google STT 비동기 API 호출",
            gcs_uri=gcs_uri,
            encoding=encoding_type.name,
            sample_rate=sample_rate
        )

        # 3. Long running operation 시작
        operation = _client.long_running_recognize(config=config, audio=audio)

        logger.info("Google STT 비동기 작업 대기 중 (최대 5분)...")
        response = operation.result(timeout=300)

        # 4. 결과 수집
        segment_count = 0
        for result in response.results:
            segment_text = result.alternatives[0].transcript
            if segment_text:
                segment_count += 1
                logger.debug(
                    "Google STT 세그먼트 (비동기)",
                    segment_number=segment_count,
                    text_preview=segment_text[:50]
                )
                yield segment_text

        logger.info(
            "Google STT 비동기 작업 완료",
            gcs_uri=gcs_uri,
            total_segments=segment_count
        )

    except Exception as e:
        logger.error("Google STT 비동기 작업 실패", exc_info=True, error=str(e))
        raise STTProcessingError(file_path=file_path, reason=str(e))

    finally:
        # 5. GCS 파일 정리
        if gcs_uri:
            _delete_from_gcs(gcs_uri)


# ==================== 공개 API ====================

def transcribe_audio(file_path: str) -> str:
    """
    Google STT로 오디오 파일 전체 변환 (자동 선택)

    Args:
        file_path: 오디오 파일 경로

    Returns:
        변환된 텍스트
    """
    _ensure_model_loaded()

    logger.info("Google STT 작업 시작", file_path=file_path)

    try:
        # ✅ 파일 크기와 길이 모두 체크
        use_sync = _should_use_sync_api(file_path)

        # 자동 선택
        if use_sync:
            logger.info("조건 충족: 동기 API 사용")
            segments = list(_transcribe_sync(file_path))
        else:
            logger.info("조건 불충족: 비동기 API 사용 (GCS 필요)")

            gcs_bucket = settings.GOOGLE_GCS_BUCKET
            if not gcs_bucket:
                raise STTProcessingError(
                    file_path=file_path,
                    reason="비동기 API를 위해 GOOGLE_GCS_BUCKET 설정이 필요합니다"
                )

            segments = list(_transcribe_long_running(file_path, gcs_bucket))

        # 전체 텍스트 조합
        full_transcript = " ".join(segments)

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
    Google STT 스트리밍 변환 (자동 선택)

    Args:
        file_path: 오디오 파일 경로

    Yields:
        세그먼트 텍스트
    """
    _ensure_model_loaded()

    logger.info("Google STT 스트리밍 작업 시작", file_path=file_path)

    try:
        # ✅ 파일 크기와 길이 모두 체크
        use_sync = _should_use_sync_api(file_path)

        # 자동 선택
        if use_sync:
            logger.info("조건 충족: 동기 API 사용")
            yield from _transcribe_sync(file_path)
        else:
            logger.info("조건 불충족: 비동기 API 사용 (GCS 필요)")

            gcs_bucket = settings.GOOGLE_GCS_BUCKET
            if not gcs_bucket:
                raise STTProcessingError(
                    file_path=file_path,
                    reason="비동기 API를 위해 GOOGLE_GCS_BUCKET 설정이 필요합니다"
                )

            yield from _transcribe_long_running(file_path, gcs_bucket)

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