import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

# ✅ 로깅 및 예외 처리 임포트
from stt_api.core.logging_config import setup_logging, get_logger
from stt_api.core.exceptions import CustomException

# 서비스 모듈
from stt_api.services.llm import llm_service
from stt_api.services.stt import whisper_service

# 라우터
from stt_api.api import batch_endpoints, stream_endpoints

# 설정
from stt_api.core.config import settings

# ✅ 로거 인스턴스 생성
logger = get_logger(__name__)


# ==================== Lifespan 이벤트 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    서버 시작/종료 시 실행되는 이벤트 핸들러
    """
    # ✅ 로깅 시스템 초기화 (가장 먼저!)
    setup_logging()

    logger.info("=" * 50)
    logger.info("CureMate API 서버 시작",
                version=settings.API_VERSION,
                env=settings.ENV)
    logger.info("=" * 50)

    try:
        # 임시 오디오 디렉터리 생성
        os.makedirs(settings.TEMP_AUDIO_DIR, exist_ok=True)
        logger.info("임시 디렉터리 확인", path=settings.TEMP_AUDIO_DIR)

        # STT 모델 로드
        logger.info("STT 모델 로드 시작", model_size=settings.STT_MODEL_SIZE)
        whisper_service.load_stt_model()
        logger.info("STT 모델 로드 완료")

        # LLM 연결 확인
        logger.info("LLM 서비스 연결 확인 시작", provider=settings.LLM_PROVIDER)
        await llm_service.check_connection()
        logger.info("LLM 서비스 연결 완료")

        logger.info("서버 초기화 완료", host=settings.HOST, port=settings.PORT)

    except Exception as e:
        logger.critical("서버 초기화 실패", exc_info=True, error=str(e))
        raise

    yield

    # --- 서버 종료 시 ---
    logger.info("서버 종료 중...")
    logger.info("=" * 50)


# ==================== FastAPI 앱 생성 ====================

app = FastAPI(
    title=settings.API_TITLE,
    description="음성 대화 STT 및 요약 비동기 API",
    version=settings.API_VERSION,
    lifespan=lifespan,
    debug=settings.DEBUG
)


# ==================== 전역 예외 핸들러 ====================

@app.exception_handler(CustomException)
async def custom_exception_handler(request: Request, exc: CustomException):
    """
    ✅ 커스텀 예외 핸들러

    모든 CustomException 일관된 형식으로 반환
    """
    logger.error(
        f"애플리케이션 예외 발생: {exc.__class__.__name__}",
        error_message=exc.message,
        error_code=exc.error_code,
        details=exc.details,
        path=request.url.path,
        method=request.method
    )

    # HTTP 상태 코드 매핑
    status_code_map = {
        "JobNotFoundException": status.HTTP_404_NOT_FOUND,
        "FileValidationError": status.HTTP_400_BAD_REQUEST,
        "FileSizeExceededError": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        "UnsupportedFileTypeError": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "LLMConnectionError": status.HTTP_503_SERVICE_UNAVAILABLE,
        "STTProcessingError": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }

    status_code = status_code_map.get(
        exc.__class__.__name__,
        status.HTTP_500_INTERNAL_SERVER_ERROR
    )

    return JSONResponse(
        status_code=status_code,
        content=exc.to_dict()
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    ✅ FastAPI 유효성 검증 예외 핸들러
    """
    logger.warning(
        "요청 유효성 검증 실패",
        errors=exc.errors(),
        path=request.url.path,
        method=request.method
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "ValidationError",
            "message": "요청 데이터가 올바르지 않습니다",
            "details": exc.errors()
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """
    ✅ 일반 예외 핸들러 (예상치 못한 오류)
    """
    logger.critical(
        "예상치 못한 오류 발생",
        exc_info=True,
        path=request.url.path,
        method=request.method,
        error=str(exc)
    )

    # 프로덕션에서는 상세 에러 숨김
    if settings.ENV == "production":
        error_detail = "내부 서버 오류가 발생했습니다"
    else:
        error_detail = str(exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "message": error_detail
        }
    )


# ==================== 미들웨어 ====================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """
    ✅ 모든 HTTP 요청/응답 로깅 미들웨어
    """
    import time

    start_time = time.time()

    # 요청 로깅
    logger.info(
        "HTTP 요청 수신",
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else "unknown"
    )

    # 요청 처리
    response = await call_next(request)

    # 응답 로깅
    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        "HTTP 응답 전송",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(duration_ms, 2)
    )

    return response


# ==================== 기본 엔드포인트 ====================

@app.get("/")
def read_root():
    """루트 엔드포인트"""
    logger.debug("루트 엔드포인트 호출")
    return {
        "message": "CureMate API is running!",
        "version": settings.API_VERSION,
        "environment": settings.ENV
    }


@app.get("/health")
async def health_check():
    """
    ✅ 헬스 체크 엔드포인트

    서비스 상태 확인용 (로드 밸런서, 모니터링 도구)
    """
    from stt_api.services.storage import job_manager

    # Redis 연결 확인
    redis_status = "ok"
    try:
        if not job_manager.cache.redis_client:
            redis_status = "unavailable"
    except:
        redis_status = "error"

    # STT 모델 로드 확인
    stt_status = "ok" if whisper_service._model else "not_loaded"

    health_data = {
        "status": "healthy",
        "version": settings.API_VERSION,
        "services": {
            "api": "ok",
            "redis": redis_status,
            "stt": stt_status,
            "llm": settings.LLM_PROVIDER
        }
    }

    # 하나라도 문제가 있으면 503 반환
    if any(v != "ok" for k, v in health_data["services"].items() if k != "llm"):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=health_data
        )

    return health_data


# ==================== 라우터 등록 ====================

app.include_router(
    batch_endpoints.router,
    tags=["Batch Conversation (File)"]
)

app.include_router(
    stream_endpoints.router,
    tags=["Real-time Stream (WebSocket)"]
)

# ==================== 서버 시작 정보 ====================

if __name__ == "__main__":
    import uvicorn

    logger.info("Uvicorn 서버 시작 (직접 실행 모드)")

    uvicorn.run(
        "stt_api.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )