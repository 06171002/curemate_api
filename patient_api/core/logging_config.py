# patient_api/core/logging_config.py

"""
구조화된 로깅 설정

JSON 형식의 로그를 출력하여 ELK, Datadog 등의
로그 분석 도구와 쉽게 통합할 수 있습니다.
"""

import logging
import sys
import json
from datetime import datetime
from typing import Any, Dict, Optional
from patient_api.core.config import settings


class JSONFormatter(logging.Formatter):
    """
    JSON 형식으로 로그를 출력하는 Formatter

    출력 예시:
    {
        "timestamp": "2025-01-01T12:00:00.000Z",
        "level": "INFO",
        "logger": "patient_api.services.stt",
        "message": "STT 작업 시작",
        "job_id": "abc-123",
        "duration_ms": 1234
    }
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 추가 컨텍스트 정보 (extra)
        if hasattr(record, "extra_data"):
            log_data.update(record.extra_data)

        # 예외 정보
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # 스택 트레이스
        if record.stack_info:
            log_data["stack_trace"] = self.formatStack(record.stack_info)

        return json.dumps(log_data, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """
    개발 환경용 컬러 로그 Formatter

    출력 예시:
    2025-01-01 12:00:00 | INFO     | patient_api.services.stt | STT 작업 시작
    """

    # ANSI 컬러 코드
    COLORS = {
        'DEBUG': '\033[36m',  # Cyan
        'INFO': '\033[32m',  # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',  # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'  # Reset
    }

    def format(self, record: logging.LogRecord) -> str:
        # 기본 포맷
        log_fmt = (
            "%(asctime)s | "
            "%(levelname)-8s | "
            "%(name)s | "
            "%(message)s"
        )

        # 예외 정보가 있으면 추가
        if record.exc_info:
            log_fmt += "\n%(exc_text)s"

        # 컬러 적용
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        colored_levelname = f"{color}{record.levelname}{self.COLORS['RESET']}"

        # levelname을 컬러 버전으로 교체
        record.levelname = colored_levelname

        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


def setup_logging(use_json: bool = None) -> None:
    """
    로깅 초기 설정

    Args:
        use_json: True면 JSON 포맷, False면 컬러 포맷 (None이면 환경에 따라 자동 결정)
    """
    # 환경에 따라 자동 결정
    if use_json is None:
        use_json = settings.ENV == "production"

    # 포맷터 선택
    if use_json:
        formatter = JSONFormatter()
    else:
        formatter = ColoredFormatter()

    # 핸들러 설정
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))
    root_logger.handlers.clear()  # 기존 핸들러 제거
    root_logger.addHandler(handler)

    # 외부 라이브러리 로그 레벨 조정 (노이즈 감소)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("celery").setLevel(logging.INFO)

    # 초기화 로그
    logger = logging.getLogger(__name__)
    logger.info(
        "로깅 시스템 초기화 완료",
        extra={
            "extra_data": {
                "log_level": settings.LOG_LEVEL,
                "format": "JSON" if use_json else "Colored",
                "env": settings.ENV
            }
        }
    )


class StructuredLogger:
    """
    구조화된 로그를 쉽게 남길 수 있는 헬퍼 클래스

    사용 예시:
        logger = StructuredLogger(__name__)
        logger.info("작업 시작", job_id="abc-123", user_id=42)
    """

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def _log(
            self,
            level: int,
            message: str,
            exc_info: bool = False,
            **context: Any
    ) -> None:
        """내부 로그 메서드"""
        # extra를 통해 추가 컨텍스트 전달
        extra = {"extra_data": context} if context else {}
        self.logger.log(level, message, exc_info=exc_info, extra=extra)

    def debug(self, message: str, **context: Any) -> None:
        """디버그 로그"""
        self._log(logging.DEBUG, message, **context)

    def info(self, message: str, **context: Any) -> None:
        """정보 로그"""
        self._log(logging.INFO, message, **context)

    def warning(self, message: str, **context: Any) -> None:
        """경고 로그"""
        self._log(logging.WARNING, message, **context)

    def error(
            self,
            message: str,
            exc_info: bool = False,
            **context: Any
    ) -> None:
        """에러 로그"""
        self._log(logging.ERROR, message, exc_info=exc_info, **context)

    def critical(
            self,
            message: str,
            exc_info: bool = False,
            **context: Any
    ) -> None:
        """치명적 에러 로그"""
        self._log(logging.CRITICAL, message, exc_info=exc_info, **context)

    def exception(self, message: str, **context: Any) -> None:
        """
        예외 로그 (스택 트레이스 포함)

        주의: try-except 블록 내에서만 사용!
        """
        self._log(logging.ERROR, message, exc_info=True, **context)


# ==================== 편의 함수 ====================

def get_logger(name: str) -> StructuredLogger:
    """
    구조화된 로거 인스턴스 반환

    Args:
        name: 로거 이름 (보통 __name__ 사용)

    Returns:
        StructuredLogger 인스턴스

    Example:
        logger = get_logger(__name__)
        logger.info("작업 시작", job_id="abc-123")
    """
    return StructuredLogger(name)


def log_function_call(func):
    """
    함수 호출을 자동으로 로깅하는 데코레이터

    Example:
        @log_function_call
        def process_data(data):
            ...
    """
    import functools
    import time

    logger = get_logger(func.__module__)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()

        logger.info(
            f"함수 호출 시작: {func.__name__}",
            function=func.__name__,
            module=func.__module__
        )

        try:
            result = wrapper(*args, **kwargs)
            duration_ms = (time.time() - start_time) * 1000

            logger.info(
                f"함수 호출 완료: {func.__name__}",
                function=func.__name__,
                duration_ms=round(duration_ms, 2)
            )

            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            logger.error(
                f"함수 호출 실패: {func.__name__}",
                exc_info=True,
                function=func.__name__,
                duration_ms=round(duration_ms, 2),
                error=str(e)
            )
            raise

    return wrapper


# ==================== 컨텍스트 매니저 ====================

class LogContext:
    """
    컨텍스트 매니저로 로그 범위 지정

    Example:
        with LogContext("STT 처리", job_id="abc-123"):
            # 여기서 발생하는 모든 예외를 자동으로 로깅
            transcribe_audio(file_path)
    """

    def __init__(self, operation: str, logger_name: str = None, **context: Any):
        self.operation = operation
        self.context = context
        self.logger = get_logger(logger_name or __name__)
        self.start_time = None

    def __enter__(self):
        self.start_time = datetime.utcnow()
        self.logger.info(f"{self.operation} 시작", **self.context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (datetime.utcnow() - self.start_time).total_seconds() * 1000

        if exc_type is None:
            # 성공
            self.logger.info(
                f"{self.operation} 완료",
                duration_ms=round(duration_ms, 2),
                **self.context
            )
        else:
            # 실패
            self.logger.error(
                f"{self.operation} 실패",
                exc_info=True,
                duration_ms=round(duration_ms, 2),
                error_type=exc_type.__name__,
                error_message=str(exc_val),
                **self.context
            )

        # False를 반환하면 예외를 다시 raise
        return False