from .base_llm_service import BaseLLMService, LLMServiceError, LLMConnectionError
from .ollama_service import ollama_service
from .lm_service import lm_service
from .gemini_service import gemini_service  # ✅ 추가
from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)

LLM_PROVIDER = settings.LLM_PROVIDER

if LLM_PROVIDER == "ollama":
    llm_service = ollama_service
elif LLM_PROVIDER == "gemini":  # ✅ 추가
    llm_service = gemini_service
else:
    llm_service = lm_service

logger.info("LLM 서비스 프로바이더 선택됨", provider=LLM_PROVIDER)


# ✅ mode 파라미터를 받는 팩토리 함수 추가
def get_llm_service(mode: str = None) -> BaseLLMService:
    """
    mode에 따라 적절한 LLM 서비스 반환

    Args:
        mode: "google" 또는 None

    Returns:
        LLM 서비스 인스턴스
    """
    if mode == "google":
        return gemini_service

    # 기본 동작
    if LLM_PROVIDER == "ollama":
        return ollama_service
    elif LLM_PROVIDER == "gemini":
        return gemini_service
    else:
        return lm_service


__all__ = [
    "llm_service",
    "ollama_service",
    "lm_service",
    "gemini_service",  # ✅ 추가
    "get_llm_service",  # ✅ 추가
    "BaseLLMService",
    "LLMServiceError"
]