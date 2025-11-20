"""
커스텀 예외 정의

모든 애플리케이션 예외는 CustomException을 상속받아야 합니다.
이를 통해 일관된 예외 처리 및 로깅이 가능합니다.
"""

from typing import Optional, Dict, Any


class CustomException(Exception):
    """
    Base exception for all application errors

    Attributes:
        message: 에러 메시지
        details: 추가 상세 정보
        error_code: 에러 코드 (선택)
    """

    def __init__(
            self,
            message: str,
            details: Optional[Dict[str, Any]] = None,
            error_code: Optional[str] = None
    ):
        self.message = message
        self.details = details or {}
        self.error_code = error_code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """예외를 딕셔너리로 변환 (API 응답용)"""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
            "error_code": self.error_code
        }


# ==================== Storage 관련 예외 ====================

class StorageException(CustomException):
    """Storage layer base exception"""
    pass


class JobNotFoundException(StorageException):
    """Job not found in storage"""

    def __init__(self, job_id: str):
        super().__init__(
            message=f"Job {job_id}를 찾을 수 없습니다",
            details={"job_id": job_id},
            error_code="JOB_NOT_FOUND"
        )


class JobCreationError(StorageException):
    """Failed to create job"""

    def __init__(self, job_id: str, reason: str):
        super().__init__(
            message=f"작업 생성 실패: {reason}",
            details={"job_id": job_id, "reason": reason},
            error_code="JOB_CREATION_FAILED"
        )


class RedisConnectionError(StorageException):
    """Redis connection failed"""

    def __init__(self, details: str):
        super().__init__(
            message="Redis 연결 실패",
            details={"error": details},
            error_code="REDIS_CONNECTION_ERROR"
        )


# ==================== STT 관련 예외 ====================

class STTException(CustomException):
    """STT service base exception"""
    pass


class ModelNotLoadedError(STTException):
    """STT model not loaded"""

    def __init__(self):
        super().__init__(
            message="STT 모델이 로드되지 않았습니다",
            error_code="MODEL_NOT_LOADED"
        )


class STTProcessingError(STTException):
    """STT processing failed"""

    def __init__(self, file_path: str, reason: str):
        super().__init__(
            message=f"STT 처리 실패: {reason}",
            details={"file_path": file_path, "reason": reason},
            error_code="STT_PROCESSING_FAILED"
        )


class AudioFormatError(STTException):
    """Invalid audio format"""

    def __init__(self, expected: str, actual: str):
        super().__init__(
            message="오디오 형식이 올바르지 않습니다",
            details={"expected": expected, "actual": actual},
            error_code="INVALID_AUDIO_FORMAT"
        )


# ==================== LLM 관련 예외 ====================

class LLMException(CustomException):
    """LLM service base exception"""
    pass


class LLMConnectionError(LLMException):
    """Failed to connect to LLM service"""

    def __init__(self, service_name: str, reason: str):
        super().__init__(
            message=f"{service_name} 연결 실패: {reason}",
            details={"service": service_name, "reason": reason},
            error_code="LLM_CONNECTION_FAILED"
        )


class LLMResponseError(LLMException):
    """Failed to parse LLM response"""

    def __init__(self, raw_response: str, reason: str):
        super().__init__(
            message=f"LLM 응답 파싱 실패: {reason}",
            details={
                "raw_response": raw_response[:200],  # 처음 200자만
                "reason": reason
            },
            error_code="LLM_RESPONSE_PARSE_ERROR"
        )


class LLMTimeoutError(LLMException):
    """LLM request timeout"""

    def __init__(self, timeout_seconds: float):
        super().__init__(
            message=f"LLM 요청 타임아웃 ({timeout_seconds}초)",
            details={"timeout": timeout_seconds},
            error_code="LLM_TIMEOUT"
        )


# ==================== File/API 관련 예외 ====================

class FileException(CustomException):
    """File handling base exception"""
    pass


class FileValidationError(FileException):
    """File validation failed"""

    def __init__(self, filename: str, reason: str):
        super().__init__(
            message=f"파일 검증 실패: {reason}",
            details={"filename": filename, "reason": reason},
            error_code="FILE_VALIDATION_FAILED"
        )


class FileSizeExceededError(FileException):
    """File size exceeds limit"""

    def __init__(self, size: int, max_size: int):
        super().__init__(
            message=f"파일 크기가 제한을 초과했습니다",
            details={
                "size_mb": round(size / 1024 / 1024, 2),
                "max_size_mb": round(max_size / 1024 / 1024, 2)
            },
            error_code="FILE_SIZE_EXCEEDED"
        )


class UnsupportedFileTypeError(FileException):
    """Unsupported file type"""

    def __init__(self, file_type: str, allowed_types: list):
        super().__init__(
            message=f"지원하지 않는 파일 형식입니다",
            details={
                "file_type": file_type,
                "allowed_types": allowed_types
            },
            error_code="UNSUPPORTED_FILE_TYPE"
        )


# ==================== Pipeline 관련 예외 ====================

class PipelineException(CustomException):
    """Pipeline execution base exception"""
    pass


class PipelineExecutionError(PipelineException):
    """Pipeline execution failed"""

    def __init__(self, job_id: str, stage: str, reason: str):
        super().__init__(
            message=f"파이프라인 실행 실패 ({stage} 단계): {reason}",
            details={
                "job_id": job_id,
                "stage": stage,
                "reason": reason
            },
            error_code="PIPELINE_EXECUTION_FAILED"
        )


# ==================== WebSocket 관련 예외 ====================

class WebSocketException(CustomException):
    """WebSocket base exception"""
    pass


class WebSocketConnectionError(WebSocketException):
    """WebSocket connection failed"""

    def __init__(self, job_id: str, reason: str):
        super().__init__(
            message=f"WebSocket 연결 실패: {reason}",
            details={"job_id": job_id, "reason": reason},
            error_code="WEBSOCKET_CONNECTION_FAILED"
        )


class InvalidAudioChunkError(WebSocketException):
    """Invalid audio chunk received"""

    def __init__(self, expected_size: int, actual_size: int):
        super().__init__(
            message="잘못된 오디오 청크 수신",
            details={
                "expected_bytes": expected_size,
                "actual_bytes": actual_size
            },
            error_code="INVALID_AUDIO_CHUNK"
        )