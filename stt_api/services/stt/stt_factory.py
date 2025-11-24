"""
STT 서비스 팩토리

설정에 따라 적절한 STT 엔진을 선택합니다.
- faster-whisper (기존)
- whisperlivekit (신규)
"""

from typing import Literal
from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


class STTService:
    """
    STT 서비스 인터페이스 통합 클래스

    설정에 따라 faster-whisper 또는 whisperlivekit을 사용
    """

    def __init__(self):
        self.engine_type = settings.STT_ENGINE
        self._load_engine()

    def _load_engine(self):
        """설정에 따라 엔진 로드"""
        if self.engine_type == "faster-whisper":
            from stt_api.services.stt import whisper_service as engine
            logger.info("STT 엔진 선택: faster-whisper (기존)")
        elif self.engine_type == "whisperlivekit":
            from stt_api.services.stt import whisperlive_service as engine
            logger.info("STT 엔진 선택: WhisperLiveKit (신규)")
        else:
            raise ValueError(f"지원하지 않는 STT 엔진: {self.engine_type}")

        self._engine = engine

    def load_stt_model(self):
        """모델 로드"""
        return self._engine.load_stt_model()

    def transcribe_audio(self, file_path: str) -> str:
        """전체 파일 변환"""
        return self._engine.transcribe_audio(file_path)

    def transcribe_audio_streaming(self, file_path: str):
        """스트리밍 변환"""
        return self._engine.transcribe_audio_streaming(file_path)

    def transcribe_segment_from_bytes(
            self,
            audio_bytes: bytes,
            initial_prompt: str = None
    ) -> str:
        """바이트 세그먼트 변환"""
        return self._engine.transcribe_segment_from_bytes(
            audio_bytes,
            initial_prompt
        )

    @property
    def _model(self):
        """모델 상태 확인용"""
        return getattr(self._engine, '_model', None) or \
            getattr(self._engine, '_engine', None)


# 전역 인스턴스 생성
stt_service = STTService()


# 편의 함수들 (하위 호환성)
def load_stt_model():
    return stt_service.load_stt_model()


def transcribe_audio(file_path: str) -> str:
    return stt_service.transcribe_audio(file_path)


def transcribe_audio_streaming(file_path: str):
    return stt_service.transcribe_audio_streaming(file_path)


def transcribe_segment_from_bytes(
        audio_bytes: bytes,
        initial_prompt: str = None
) -> str:
    return stt_service.transcribe_segment_from_bytes(
        audio_bytes,
        initial_prompt
    )