"""
STT 서비스 모듈

설정에 따라 적절한 엔진을 자동 선택합니다.
"""

# ✅ 팩토리를 통한 자동 엔진 선택
from .stt_factory import (
    stt_service,
    load_stt_model,
    transcribe_audio,
    transcribe_audio_streaming,
    transcribe_segment_from_bytes
)

from .vad_processor import VADProcessor

# ✅ 직접 엔진 import도 가능 (테스트/디버깅용)
from . import whisper_service

try:
    from . import whisperlive_service
except ImportError:
    whisperlive_service = None  # whisperlivekit 미설치 시

__all__ = [
    # 기본 인터페이스 (자동 엔진 선택)
    "stt_service",
    "load_stt_model",
    "transcribe_audio",
    "transcribe_audio_streaming",
    "transcribe_segment_from_bytes",
    "VADProcessor",

    # 개별 엔진 (선택적 사용)
    "whisper_service",
    "whisperlive_service",
]