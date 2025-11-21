from .base_llm_service import BaseLLMService, LLMServiceError, LLMConnectionError
from .ollama_service import ollama_service
from .lm_service import lm_service
from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


LLM_PROVIDER = settings.LLM_PROVIDER

if LLM_PROVIDER == "ollama":
    llm_service = ollama_service
else:
    llm_service = lm_service

logger.info("LLM 서비스 프로바이더 선택됨", provider=LLM_PROVIDER)

__all__ = [
    "llm_service",
    "ollama_service",
    "lm_service",
    "BaseLLMService",
    "LLMServiceError"
]