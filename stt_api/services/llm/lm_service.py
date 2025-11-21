import json
import sys
from typing import Dict, Any
from openai import AsyncOpenAI
import httpx
import re
from .base_llm_service import BaseLLMService, LLMConnectionError, LLMResponseError
from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)

# --- 1. LM Studio 설정 ---
LMSTUDIO_BASE_URL = settings.LMSTUDIO_BASE_URL
LMSTUDIO_HEALTH_URL = settings.LMSTUDIO_BASE_URL.replace("/v1", "")

_client = AsyncOpenAI(
    base_url=LMSTUDIO_BASE_URL,
    api_key="lm-studio",
    timeout=settings.LMSTUDIO_TIMEOUT
)


async def check_llm_connection():
    """
    FastAPI 서버 시작 시 LM Studio 서버가 켜져 있는지 확인합니다.
    """
    try:
        logger.info("[LLM Service] LM Studio 서버 연결을 시도합니다...")
        async with httpx.AsyncClient() as client:
            response = await client.get(LMSTUDIO_HEALTH_URL)
            response.raise_for_status()
        logger.info("[LLM Service] LM Studio 서버 연결 성공.")
    except httpx.RequestError as e:
        logger.error(
            "LM Studio 서버 연결 실패",
            error=str(e),
            hint="LM Studio가 Windows에서 0.0.0.0 호스트로 실행 중인지 확인하세요"
        )
    except Exception as e:
        logger.error("LM Studio 연결 중 알 수 없는 오류", exc_info=True, error=str(e))


# --- 2. 프롬프트 템플릿 ---

def _build_simple_summary_prompt(transcript_text: str) -> str:
    """
    간단한 요약 프롬프트 (테스트용)
    """
    prompt = f"""
아래 문장을 요약해주세요.

[텍스트]
{transcript_text}

[요약 형식 (JSON)]
{{
  "summary": "요약된 내용"
}}

[지침]
* JSON 형식만 응답으로 반환하세요.
* 마크다운, 코드 블록, 추가 설명 없이 순수 JSON만 출력하세요.
"""
    return prompt


def _build_medical_summary_prompt(transcript_text: str) -> str:
    """
    의료 대화 요약 프롬프트 (프로덕션용)
    """
    prompt = f"""
당신은 의사와 환자의 대화록을 분석하는 전문 의료 비서입니다.
다음 대화 내용을 바탕으로 핵심 내용을 요약해 주세요.

[대화록]
{transcript_text}

[요약 형식 (JSON)]
{{
  "main_complaint": "환자가 호소하는 주요 증상",
  "diagnosis": "의사의 소견 및 진단명",
  "recommendation": "처방, 검사 계획, 또는 생활 권고 사항"
}}

[지침]
* JSON 형식만 응답으로 반환하세요.
* 마크다운, 코드 블록, 추가 설명 없이 순수 JSON만 출력하세요.
* 각 항목은 간결하게 1-2문장으로 작성하세요.

[응답 예시]
{{
  "main_complaint": "3일 전부터 지속되는 두통과 어지러움",
  "diagnosis": "편두통 의심, 혈압 정상 범위",
  "recommendation": "타이레놀 복용, 충분한 휴식, 1주일 후 재방문"
}}
"""
    return prompt


def _build_structured_extraction_prompt(transcript_text: str) -> str:
    """
    구조화된 정보 추출 프롬프트 (상세 버전)
    """
    prompt = f"""
당신은 의료 대화록을 구조화된 데이터로 변환하는 전문 AI입니다.
다음 대화 내용에서 필요한 정보를 추출해 주세요.

[대화록]
{transcript_text}

[추출 형식 (JSON)]
{{
  "patient_info": {{
    "age": "환자 나이 (언급되지 않으면 null)",
    "gender": "환자 성별 (언급되지 않으면 null)"
  }},
  "symptoms": [
    "증상1",
    "증상2"
  ],
  "duration": "증상 지속 기간",
  "severity": "증상 심각도 (경증/중등도/중증)",
  "diagnosis": "의사의 진단명",
  "prescription": [
    "처방 약물1",
    "처방 약물2"
  ],
  "tests_ordered": [
    "검사 항목1"
  ],
  "follow_up": "추후 관리 계획",
  "lifestyle_advice": "생활 습관 권고 사항"
}}

[지침]
* JSON 형식만 응답으로 반환하세요.
* 언급되지 않은 항목은 null 또는 빈 배열([])로 표시하세요.
* 배열 항목은 간결하게 작성하세요.
"""
    return prompt


# --- 3. JSON 파싱 유틸리티 ---

def _parse_json_response(raw_response: str) -> Dict[str, Any]:
    """
    LM Studio 응답에서 JSON을 안전하게 파싱합니다.
    """
    if not raw_response:
        raise ValueError("LM Studio가 비어있는 응답을 반환했습니다.")

    try:
        # 1. 마크다운 코드 블록 제거
        cleaned = re.sub(r'```json\s*|\s*```', '', raw_response)
        cleaned = re.sub(r'"""\s*|\s*"""', '', cleaned)
        cleaned = cleaned.strip()

        # 2. 첫 번째 완전한 JSON 객체만 추출
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', cleaned, re.DOTALL)

        if not match:
            logger.error(
                "JSON 파싱 실패: JSON 객체를 찾을 수 없음",
                raw_response_preview=raw_response[:200]
            )
            raise ValueError("응답에서 JSON 객체를 찾을 수 없습니다.")

        json_string = match.group(0).strip()

        # 3. JSON 파싱
        parsed_data = json.loads(json_string)

        logger.info("JSON 파싱 성공", keys=list(parsed_data.keys()))
        return parsed_data

    except json.JSONDecodeError as e:
        logger.error(
            "JSON 디코딩 실패",
            error=str(e),
            json_string_preview=json_string[:200] if 'json_string' in locals() else None,
            raw_response_preview=raw_response[:200]
        )
        raise ValueError("LM Studio가 반환한 응답이 올바른 JSON 형식이 아닙니다.")


# --- 4. LLM 호출 래퍼 ---

async def _call_llm(prompt: str, temperature: float = 0.0) -> str:
    """
    LM Studio API를 호출하는 공통 함수
    """
    try:
        response = await _client.chat.completions.create(
            model="local-model",
            messages=[
                {"role": "system", "content": prompt}
            ],
            temperature=temperature,
        )

        raw_response = response.choices[0].message.content
        return raw_response

    except httpx.RequestError as e:
        logger.error("LM Studio 연결 오류", error=str(e))
        raise RuntimeError(f"LM Studio 서비스에 연결할 수 없습니다: {e}")
    except Exception as e:
        logger.error("LLM 호출 중 알 수 없는 오류", exc_info=True, error=str(e))
        raise e


# --- 5. 공개 API 함수들 ---

async def get_simple_summary(transcript: str) -> Dict[str, Any]:
    """
    간단한 요약 (테스트용)

    반환 형식:
    {
        "summary": "요약된 내용"
    }
    """
    logger.info("[LLM Service] 간단 요약 작업 시작...")

    prompt = _build_simple_summary_prompt(transcript)
    raw_response = await _call_llm(prompt, temperature=0.0)
    summary_dict = _parse_json_response(raw_response)

    # 필수 키 검증
    if "summary" not in summary_dict:
        raise ValueError("응답에 'summary' 키가 없습니다.")

    logger.info("[LLM Service] 간단 요약 완료")
    return summary_dict


async def get_medical_summary(transcript: str) -> Dict[str, Any]:
    """
    의료 대화 요약 (프로덕션용)

    반환 형식:
    {
        "main_complaint": "환자가 호소하는 주요 증상",
        "diagnosis": "의사의 소견 및 진단명",
        "recommendation": "처방, 검사 계획, 또는 생활 권고 사항"
    }
    """
    logger.info("[LLM Service] 의료 요약 작업 시작...")

    prompt = _build_medical_summary_prompt(transcript)
    raw_response = await _call_llm(prompt, temperature=0.0)
    summary_dict = _parse_json_response(raw_response)

    # 필수 키 검증
    required_keys = ["main_complaint", "diagnosis", "recommendation"]
    missing_keys = [key for key in required_keys if key not in summary_dict]

    if missing_keys:
        logger.warning("응답에 누락된 키", key=missing_keys)
        # 누락된 키를 빈 문자열로 채움 (유연한 처리)
        for key in missing_keys:
            summary_dict[key] = ""

    logger.info("의료 요약 완료")
    return summary_dict


async def get_structured_data(transcript: str) -> Dict[str, Any]:
    """
    구조화된 정보 추출 (상세 버전)

    반환 형식:
    {
        "patient_info": {"age": "...", "gender": "..."},
        "symptoms": ["증상1", "증상2"],
        "duration": "...",
        "severity": "...",
        "diagnosis": "...",
        "prescription": ["약물1"],
        "tests_ordered": ["검사1"],
        "follow_up": "...",
        "lifestyle_advice": "..."
    }
    """
    logger.info("[LLM Service] 구조화된 데이터 추출 작업 시작...")

    prompt = _build_structured_extraction_prompt(transcript)
    raw_response = await _call_llm(prompt, temperature=0.0)
    structured_dict = _parse_json_response(raw_response)

    logger.info("[LLM Service] 구조화된 데이터 추출 완료")
    return structured_dict


# --- 6. (레거시) 기존 함수 이름 유지 (하위 호환성) ---

async def get_summary(transcript: str) -> Dict[str, Any]:
    """
    기본 요약 함수 (현재는 간단 요약 사용)

    나중에 get_medical_summary()로 변경 가능
    """
    return await get_simple_summary(transcript)


class LmService(BaseLLMService):
    """LM Studio 서비스 구현"""

    def __init__(self):
        self.base_url = LMSTUDIO_BASE_URL
        self.health_url = LMSTUDIO_HEALTH_URL
        self.client = AsyncOpenAI(base_url=self.base_url, api_key="lm-studio")

    async def check_connection(self) -> bool:
        try:
            logger.info("[LM Studio Service] 서버 연결 확인...")
            async with httpx.AsyncClient() as client:
                response = await client.get(self.health_url)
                response.raise_for_status()
            logger.info("[LM Studio Service] 연결 성공")
            return True
        except httpx.RequestError as e:
            logger.error("LM Studio 연결 실패", error=str(e))
            raise LLMConnectionError(f"LM Studio 연결 실패: {e}")

    async def get_summary(self, transcript: str) -> Dict[str, Any]:
        return await get_simple_summary(transcript)

    async def get_medical_summary(self, transcript: str) -> Dict[str, Any]:
        return await get_medical_summary(transcript)


# 전역 인스턴스
lm_service = LmService()