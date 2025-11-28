import httpx
import json
import sys
import asyncio
from typing import Dict, Any, Optional
from stt_api.core.config import settings

from .base_llm_service import BaseLLMService, LLMConnectionError, LLMResponseError
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)

# --- 1. Ollama 설정 (F-SUM-01 세부사항) ---

# (설정) Ollama API가 실행 중인 주소
OLLAMA_API_URL = f"{settings.OLLAMA_BASE_URL}/api/generate"

# (설정) Ollama에서 사용할 모델 이름 (예: "llama3", "gemma:7b")
OLLAMA_MODEL_NAME = settings.OLLAMA_MODEL_NAME

# API 호출 타임아웃 (초). 요약은 오래 걸릴 수 있으므로 넉넉하게 설정
API_TIMEOUT = settings.OLLAMA_TIMEOUT

# httpx 클라이언트는 재사용하는 것이 좋습니다.
# 비동기(async) 클라이언트를 생성합니다. (워커가 비동기로 호출할 것을 대비)
# _client = httpx.AsyncClient(timeout=API_TIMEOUT)


# --- 프롬프트 생성 (F-SUM-02) ---

def _build_summary_prompt(transcript_text: str) -> str:
    """
    명세서 F-SUM-02에 정의된 프롬프트를 생성합니다.
    요약 품질을 높이려면 이 함수만 수정하면 됩니다.
    """
    prompt = f"""
        아래 [입력 텍스트]를 읽고 전체 내용을 요약해 주세요.
        결과는 반드시 아래 [JSON 형식]으로만 출력해야 합니다.

        [입력 텍스트]
        {transcript_text}

        [JSON 형식]
        {{
            "summary": "여기에 요약된 전체 내용을 적으세요."
        }}

        [지침]
        1. 마크다운(```json)을 사용하지 말고 순수 JSON 문자열만 출력하세요.
        2. 요약은 간결하고 명확하게 작성하세요.
        """
    return prompt


class OllamaService(BaseLLMService):
    """Ollama LLM 서비스 구현"""

    def __init__(self):
        self.api_url = OLLAMA_API_URL
        self.model_name = OLLAMA_MODEL_NAME

    async def check_connection(self) -> bool:
        try:
            logger.info("[Ollama Service] 서버 연결 확인...")
            # ✅ [수정] 안전하게 로컬 클라이언트 사용
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")  # URL은 상황에 맞게
            logger.info("[Ollama Service] 연결 성공", model=self.model_name)
            return True
        except httpx.RequestError as e:
            logger.error("[Ollama Service] 연결 실패", error_msg=e)
            raise LLMConnectionError(f"Ollama 연결 실패: {e}")

    async def get_summary(self, transcript: str) -> Dict[str, Any]:
        """
        텍스트 대본을 받아 Ollama에 요약을 요청하고,
        파싱된 딕셔너리(JSON)를 반환합니다.
        """
        logger.info("[Ollama Service] 요약 작업을 시작합니다...")

        # 1. 프롬프트 생성 (F-SUM-02)
        prompt_text = _build_summary_prompt(transcript)

        MAX_RETRIES = 3

        for attempt in range(MAX_RETRIES):
            try:
                # ✅ [핵심] 재시도할 때마다 온도를 높여서 다른 결과를 유도
                # 시도 1: 0.0 (정확), 시도 2: 0.2, 시도 3: 0.4
                current_temp = 0.2 * attempt

                logger.info(
                    "[Ollama Service] 요약 요청",
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    temperature=current_temp
                )

                # 2. API 호출 Payload 구성
                payload = {
                    "model": OLLAMA_MODEL_NAME,
                    "prompt": prompt_text,
                    "stream": False,
                    "format": "json",
                    # ✅ Ollama 옵션 설정 (temperature 적용)
                    "options": {
                        "temperature": current_temp,
                        "num_predict": 1024  # 생성 토큰 길이 제한 (필요시 조절)
                    }
                }

                async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                    response = await client.post(OLLAMA_API_URL, json=payload)
                    response.raise_for_status()
                    response_json = response.json()

                    # Ollama 응답 구조: {"response": "{...}", "done": true, ...}
                    raw_response_string = response_json.get("response", "")

                # 3. 결과 파싱 및 검증
                if not raw_response_string:
                    raise ValueError("Ollama가 비어있는 응답을 반환했습니다.")

                try:
                    summary_dict = json.loads(raw_response_string)
                except json.JSONDecodeError:
                    logger.warning("[Ollama Service] JSON 파싱 실패, 재시도합니다.")
                    raise ValueError("JSON 파싱 실패")

                # ✅ 빈 딕셔너리 체크 (이것 때문에 재시도 로직을 넣은 것임)
                if not summary_dict:
                    raise ValueError("Ollama가 빈 JSON({})을 반환했습니다.")

                logger.info("[Ollama Service] 요약 성공 및 JSON 파싱 완료.")
                return summary_dict

            except Exception as e:
                logger.warning(
                    "[Ollama Service] 요약 시도 실패",
                    attempt=attempt + 1,
                    error=str(e)
                )

                # 마지막 시도였다면 에러를 던지거나 기본값 반환
                if attempt == MAX_RETRIES - 1:
                    logger.error("[Ollama Service] 최종 실패. 기본 에러 응답을 반환합니다.")
                    # [선택] 에러를 던져서 작업을 FAILED로 만들거나,
                    # raise e

                    # [권장] 빈 값이라도 채워서 반환 (Job은 완료 처리됨)
                    return {
                        "main_complaint": "요약 실패",
                        "diagnosis": "시스템 오류",
                        "recommendation": "요약 서비스 연결에 실패했습니다."
                    }

                # 잠시 대기 후 재시도
                await asyncio.sleep(1)

    async def get_medical_summary(self, transcript: str) -> Dict[str, Any]:
        # ... (기존 로직 또는 get_summary 호출)
        pass


# 전역 인스턴스
ollama_service = OllamaService()