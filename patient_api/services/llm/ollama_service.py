import httpx
import json
import sys
from typing import Dict, Any, Optional
from patient_api.core.config import settings

from .base_llm_service import BaseLLMService, LLMConnectionError, LLMResponseError

# --- 1. Ollama 설정 (F-SUM-01 세부사항) ---

# (설정) Ollama API가 실행 중인 주소
OLLAMA_API_URL = f"{settings.OLLAMA_BASE_URL}/api/generate"

# (설정) Ollama에서 사용할 모델 이름 (예: "llama3", "gemma:7b")
OLLAMA_MODEL_NAME = settings.OLLAMA_MODEL_NAME

# API 호출 타임아웃 (초). 요약은 오래 걸릴 수 있으므로 넉넉하게 설정
API_TIMEOUT = settings.OLLAMA_TIMEOUT

# httpx 클라이언트는 재사용하는 것이 좋습니다.
# 비동기(async) 클라이언트를 생성합니다. (워커가 비동기로 호출할 것을 대비)
_client = httpx.AsyncClient(timeout=API_TIMEOUT)


# --- 프롬프트 생성 (F-SUM-02) ---

def _build_summary_prompt(transcript_text: str) -> str:
    """
    명세서 F-SUM-02에 정의된 프롬프트를 생성합니다.
    요약 품질을 높이려면 이 함수만 수정하면 됩니다.
    """
    # { } 안에 변수를 넣기 위해 f-string을 사용합니다.
    # JSON 형식 자체도 { }를 포함하므로,
    # JSON의 { }는 {{ }} (두 번)으로 감싸서 f-string이 변수로 오해하지 않게 합니다.

    # prompt = f"""
    #                 당신은 의사와 환자의 대화록을 분석하는 전문 의료 비서입니다.
    #                 다음 대화 내용을 바탕으로 아래의 JSON 형식에 맞춰 핵심 내용을 요약해 주세요.
    #
    #                 [대화록]
    #                 {transcript_text}
    #
    #                 [요약 형식 (JSON)]
    #                 {{
    #                   "main_complaint": "환자가 호소하는 주요 증상 (CC)",
    #                   "diagnosis": "의사의 소견 및 진단명",
    #                   "recommendation": "처방, 검사 계획, 또는 생활 권고 사항"
    #                 }}
    #
    #                 [지침]
    #                 * 대화록에 정보가 부족한 항목은 "정보 없음" 또는 "언급되지 않음"으로 채우세요.
    #                 * [대화록] 내용을 기반으로 **사실(Fact)**만을 요약하세요.
    #                 * 응답은 반드시 JSON 형식이어야 합니다. 그 외의 설명이나 텍스트를 포함하지 마세요.
    #         """
    prompt = f"""
                        아래 문장을 요약해주세요.
                        {transcript_text}
                """
    return prompt


class OllamaService(BaseLLMService):
    """Ollama LLM 서비스 구현"""

    def __init__(self):
        self.api_url = OLLAMA_API_URL
        self.model_name = OLLAMA_MODEL_NAME
        self.client = httpx.AsyncClient(timeout=API_TIMEOUT)

    async def check_connection(self) -> bool:
        try:
            print("[Ollama Service] 🟡 서버 연결 확인...")
            await self.client.get("http://host.docker.internal:11434/api/tags")
            print(f"[Ollama Service] 🟢 연결 성공 (모델: {self.model_name})")
            return True
        except httpx.RequestError as e:
            print(f"[Ollama Service] 🔴 연결 실패: {e}", file=sys.stderr)
            raise LLMConnectionError(f"Ollama 연결 실패: {e}")

    async def get_summary(transcript: str) -> Dict[str, Any]:
        """
        텍스트 대본을 받아 Ollama에 요약을 요청하고,
        파싱된 딕셔너리(JSON)를 반환합니다.
        """
        print(f"[Ollama Service] 🔵 요약 작업을 시작합니다...")

        # 1. 프롬프트 생성 (F-SUM-02)
        prompt_text = _build_summary_prompt(transcript)

        # 2. API 호출 (F-SUM-01)
        payload = {
            "model": OLLAMA_MODEL_NAME,
            "prompt": prompt_text,
            "stream": False,
            "format": "json"  # ★★★ Ollama에 JSON 형식으로 응답하도록 강제 (파싱이 쉬워짐)
        }

        raw_response_string = None
        try:
            response = await _client.post(OLLAMA_API_URL, json=payload)

            # 4xx, 5xx 에러가 발생하면 예외를 발생시킴
            response.raise_for_status()

            # Ollama가 'format: json'을 사용하면 'response' 키에 JSON "문자열"을 담아줍니다.
            raw_response_string = response.json()["response"]

        except httpx.HTTPStatusError as e:
            print(f"[Ollama Service] 🔴 Ollama API 오류 (HTTP {e.response.status_code}): {e.response.text}",
                  file=sys.stderr)
            raise RuntimeError(f"Ollama API 오류: {e.response.text}")
        except httpx.RequestError as e:
            print(f"[Ollama Service] 🔴 Ollama 연결 오류: {e}", file=sys.stderr)
            raise RuntimeError(f"Ollama 서비스에 연결할 수 없습니다: {e}")
        except Exception as e:
            print(f"[Ollama Service] 🔴 요약 요청 중 알 수 없는 오류: {e}", file=sys.stderr)
            raise e  # worker.py가 처리하도록 예외를 다시 발생시킴

        # 3. 결과 파싱 (F-SUM-03)
        if not raw_response_string:
            print("[Ollama Service] 🔴 Ollama가 비어있는 응답을 반환했습니다.", file=sys.stderr)
            raise ValueError("Ollama가 비어있는 응답을 반환했습니다.")

        try:
            # 'format: json' 옵션 덕분에 raw_response_string 자체가
            # 깨끗한 JSON 문자열입니다.
            # (예: '{\n  "main_complaint": "복통"\n}')

            summary_dict = json.loads(raw_response_string)

            print(f"[Ollama Service] 🟢 요약 작업 완료 및 JSON 파싱 성공.")
            return summary_dict

        except json.JSONDecodeError:
            print(f"[Ollama Service] 🔴 Ollama 응답 JSON 파싱 실패!", file=sys.stderr)
            print(f"[Ollama Service] 🔴 Ollama 원본 응답: {raw_response_string}", file=sys.stderr)
            raise ValueError("Ollama가 반환한 요약이 올바른 JSON 형식이 아닙니다.")

    async def get_medical_summary(self, transcript: str) -> Dict[str, Any]:
        # ... (기존 로직 또는 get_summary 호출)
        pass


# 전역 인스턴스
ollama_service = OllamaService()