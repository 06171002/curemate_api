from pydantic_settings import BaseSettings
from typing import Dict, Literal
from functools import lru_cache


class Settings(BaseSettings):
    """
    환경별 설정 관리

    .env 파일에 값이 있으면 해당 값 사용,
    없으면 아래 기본값 사용
    """

    # ==================== 환경 설정 ====================
    ENV: Literal["development", "production", "testing"] = "development"
    DEBUG: bool = False

    # ==================== API 설정 ====================
    API_TITLE: str = "CureMate STT/Summary API"
    API_VERSION: str = "1.0.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ==================== Redis 설정 ====================
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ==================== 데이터베이스 설정 (MariaDB) ====================

    DB_TYPE: Literal["mariadb", "mysql", "postgresql"] = "mariadb"
    DB_HOST: str = "210.116.103.144"
    DB_PORT: int = 3306
    DB_NAME: str = "curemate"
    DB_USER: str = "uds"
    DB_PASSWORD: str = "275506%)"
    DB_CHARSET: str = "utf8mb4"

    # 연결 풀 설정
    DB_POOL_SIZE: int = 3  # 기본 연결 수
    DB_MAX_OVERFLOW: int = 5  # 최대 추가 연결 수
    DB_POOL_RECYCLE: int = 3600  # 연결 재사용 주기 (초)
    DB_ECHO: bool = False  # SQL 쿼리 로깅 여부

    @property
    def DATABASE_URL(self) -> str:
        """
        SQLAlchemy용 데이터베이스 URL 생성

        형식: mysql+aiomysql://user:password@host:port/database?charset=utf8mb4
        """
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?charset={self.DB_CHARSET}"
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        """
        동기 데이터베이스 URL (테스트/마이그레이션용)

        형식: mysql+pymysql://user:password@host:port/database?charset=utf8mb4
        """
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?charset={self.DB_CHARSET}"
        )

    # ==================== STT 설정 ====================
    STT_ENGINE: Literal["faster-whisper", "whisperlivekit"] = "faster-whisper"
    STT_MODEL_SIZE: Literal["tiny", "base", "small", "medium", "large-v3"] = "small"
    STT_DEVICE_TYPE: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    STT_LANGUAGE: str = "ko"

    # ✅ STT 성능 최적화 옵션
    STT_BEAM_SIZE: int = 1  # 빔 서치 크기 (1=greedy, 5=default)
    STT_COMPUTE_TYPE: str = "int8"  # int8, float16, float32

    WHISPERLIVE_USE_DIARIZATION: bool = False

    # ==================== LLM 설정 ====================
    LLM_PROVIDER: Literal["ollama", "lmstudio"] = "ollama"

    # LM Studio
    LMSTUDIO_BASE_URL: str = "http://host.docker.internal:1234/v1"
    LMSTUDIO_TIMEOUT: float = 120.0

    # Ollama
    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL_NAME: str = "gemma3"
    OLLAMA_TIMEOUT: float = 300.0

    # ==================== 파일 설정 ====================
    TEMP_AUDIO_DIR: str = "temp_audio"
    MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 100MB

    # ==================== 로깅 설정 ====================
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# ==================== 상수 (거의 안 바꾸는 값들) ====================
# 이것들은 .env로 오버라이드 불필요

class Constants:
    """애플리케이션 상수"""

    # VAD 설정 (WebRTC 표준값)
    VAD_SAMPLE_RATE = 16000
    VAD_FRAME_DURATION_MS = 30
    VAD_AGGRESSIVENESS = 3
    VAD_MIN_SPEECH_FRAMES = 3  # 최소 음성 길이 (90ms)
    VAD_MAX_SILENCE_FRAMES = 10  # 침묵 감지 시간 (150ms)

    # 파일 형식
    ALLOWED_AUDIO_EXTENSIONS = ["mp3", "wav", "m4a", "ogg", "flac"]

    # Celery
    CELERY_TIMEZONE = "Asia/Seoul"
    CELERY_WORKER_CONCURRENCY = 4

    # ✅ 스트리밍 파이프라인
    STREAM_MAX_WORKERS = 3  # STT 병렬 처리 워커 수


constants = Constants()


# ==================== 인메모리 상태 관리 ====================
# (StreamingJob 관리용)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stt_api.domain.streaming_job import StreamingJob

active_jobs: Dict[str, "StreamingJob"] = {}
