from faster_whisper import WhisperModel
from typing import Optional, Generator
import numpy as np
import time

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
        compute_type=settings.STT_COMPUTE_TYPE
    )

    try:
        _model = WhisperModel(
            settings.STT_MODEL_SIZE,
            device=settings.STT_DEVICE_TYPE,
            compute_type=settings.STT_COMPUTE_TYPE,
            # ✅ 추가 최적화 옵션
            cpu_threads=8,  # CPU 스레드 수
            num_workers=1  # 워커 수
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

    total_start = time.perf_counter()

    logger.info(
        "세그먼트 STT 시작",
        audio_size_bytes=len(audio_bytes),
        has_context=bool(initial_prompt)
    )

    try:
        # 1. NumPy 변환
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float32 = audio_np.astype(np.float32) / 32768.0

        # ✅ 1-1. 소음 감지 (RMS 에너지 체크)
        rms_energy = np.sqrt(np.mean(audio_float32 ** 2))

        # RMS가 너무 낮으면 소음으로 간주 (조정 가능)
        MIN_RMS_THRESHOLD = 0.01
        if rms_energy < MIN_RMS_THRESHOLD:
            logger.debug(
                "세그먼트 건너뜀 (소음 감지)",
                rms_energy=round(rms_energy, 4),
                threshold=MIN_RMS_THRESHOLD
            )
            return ""

        # 2. STT 실행 (최적화된 파라미터)
        segments, info = _model.transcribe(
            audio_float32,
            language=settings.STT_LANGUAGE,
            vad_filter=False,  # VAD는 이미 적용됨
            initial_prompt=initial_prompt,
            beam_size=settings.STT_BEAM_SIZE,  # ✅ 1로 설정 시 가장 빠름
            best_of=1,  # ✅ 샘플링 횟수 최소화
            temperature=0.0,  # ✅ 그리디 디코딩
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.6,
            condition_on_previous_text=False,
        )

        # 3. 결과 수집
        segment_texts = [seg.text.strip() for seg in segments]
        result_text = " ".join(segment_texts)

        # ✅ [추가] 후처리: 반복 텍스트 필터링
        if _is_hallucination(result_text):
            logger.warning(f"환각 텍스트 감지되어 무시됨: {result_text}")
            return ""

        total_time = (time.perf_counter() - total_start) * 1000

        logger.info(
            "세그먼트 STT 완료",
            audio_size_bytes=len(audio_bytes),
            result_length=len(result_text),
            text_preview=result_text[:50],
            total_ms=round(total_time, 2)
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


# ✅ [추가] 헬퍼 함수
def _is_hallucination(text: str) -> bool:
    if not text:
        return False

    # 1. 특정 반복 패턴 강제 차단
    ban_list = ["아...", "아 아", "오오", "코코", "MBC", "구독", "좋아요"]
    for ban in ban_list:
        if text.count(ban) >= 2:  # 2번 이상 반복되면 차단
            return True

    # 2. 문자 다양성 체크 (길이에 비해 사용된 문자가 너무 적으면 반복으로 간주)
    # 예: "ㅋㅋㅋㅋㅋㅋ" -> 길이 6, 고유문자 1({'ㅋ'}) -> 비율 0.16
    if len(text) > 10:
        unique_ratio = len(set(text.replace(" ", ""))) / len(text)
        if unique_ratio < 0.2:  # 고유 문자가 20% 미만이면 환각일 확률 높음
            return True

    return False