from .base_llm_service import BaseLLMService, LLMServiceError, LLMConnectionError
from .ollama_service import ollama_service
from .lm_service import lm_service
from patient_api.core.config import settings


LLM_PROVIDER = settings.LLM_PROVIDER

if LLM_PROVIDER == "ollama":
    llm_service = ollama_service
else:
    llm_service = lm_service

print(f"[LLM Service] 🟢 Provider: {LLM_PROVIDER}")

__all__ = [
    "llm_service",
    "ollama_service",
    "lm_service",
    "BaseLLMService",
    "LLMServiceError"
]