from .base_llm_service import BaseLLMService, LLMServiceError, LLMConnectionError
from .ollama_service import ollama_service
from .lm_service import lm_service

# ✅ 환경 변수로 사용할 LLM 선택
import os
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "lmstudio")  # "ollama" or "lmstudio"

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