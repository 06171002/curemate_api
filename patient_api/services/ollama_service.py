# patient_api/services/llm_service.py

import json
import sys
from typing import Dict, Any
from openai import OpenAI, AsyncOpenAI
import httpx

# --- 1. LM Studio 설정 ---
# (LM Studio는 1234 포트를 기본으로 사용)
LMSTUDIO_BASE_URL = "http://host.docker.internal:1234/v1"
LMSTUDIO_HEALTH_URL = "http://host.docker.internal:1234"

# (★수정) OpenAI 클라이언트 사용
_client = AsyncOpenAI(
    base_url=LMSTUDIO_BASE_URL,
    api_key="lm-studio"  # (LM Studio는 API 키가 필요 없지만, 형식상 아무 값이나 입력)
)


async def check_llm_connection():
    """
    FastAPI 서버 시작 시 LM Studio 서버가 켜져 있는지 확인합니다.
    """
    try:
        print("[LLM Service] 🟡 LM Studio 서버 연결을 시도합니다...")
        async with httpx.AsyncClient() as client:
            response = await client.get(LMSTUDIO_HEALTH_URL)
            response.raise_for_status()
        print(f"[LLM Service] 🟢 LM Studio 서버 연결 성공.")
    except httpx.RequestError as e:
        print(f"[LLM Service] 🔴 LM Studio 서버 연결 실패: {e}", file=sys.stderr)
        print("[LLM Service] 🔴 LM Studio가 Windows에서 0.0.0.0 호스트로 실행 중인지 확인하세요.", file=sys.stderr)
    except Exception as e:
        print(f"[LLM Service] 🔴 LM Studio 연결 중 알 수 없는 오류: {e}", file=sys.stderr)


# --- 2. 프롬프트 생성 ---
def _build_summary_prompt(transcript_text: str) -> str:
    """
    (★수정) Ollama 프롬프트를 JSON 구조화 프롬프트로 변경
    """
    prompt = f"""
            당신은 의사와 환자의 대화록을 분석하는 전문 의료 비서입니다.
            다음 대화 내용을 바탕으로 아래의 JSON 형식에 맞춰 핵심 내용을 요약해 주세요.
            [대화록]
            {transcript_text}
            [요약 형식 (JSON)]
            {{
              "main_complaint": "환자가 호소하는 주요 증상 (CC)",
              "diagnosis": "의사의 소견 및 진단명",
              "recommendation": "처방, 검사 계획, 또는 생활 권고 사항"
            }}
            [지침]
            * JSON 형식만 응답으로 반환하세요.
    """
    return prompt


# --- 3. 핵심 기능: 요약 요청 함수 ---
async def get_summary(transcript: str) -> Dict[str, Any]:
    """
    (★수정) OpenAI 호환 API를 사용하여 요약을 요청합니다.
    """
    print(f"[LLM Service] 🔵 요약 작업을 시작합니다...")

    system_prompt = _build_summary_prompt(transcript)

    try:
        # (★수정) OpenAI API 형식으로 호출
        response = await _client.chat.completions.create(
            model="local-model",  # (LM Studio에서는 이 값이 무시됨)
            messages=[
                {"role": "system", "content": system_prompt}
            ],
            temperature=0.0,
        )

        raw_response_string = response.choices[0].message.content

    except httpx.RequestError as e:
        print(f"[LLM Service] 🔴 LM Studio 연결 오류: {e}", file=sys.stderr)
        raise RuntimeError(f"LM Studio 서비스에 연결할 수 없습니다: {e}")
    except Exception as e:
        print(f"[LLM Service] 🔴 요약 요청 중 알 수 없는 오류: {e}", file=sys.stderr)
        raise e

    # ... (이하 json.loads()를 사용한 파싱 로직은 동일)
    if not raw_response_string:
        # ...
        raise ValueError("LM Studio가 비어있는 응답을 반환했습니다.")

    try:
        summary_dict = json.loads(raw_response_string)
        print(f"[LLM Service] 🟢 요약 작업 완료 및 JSON 파싱 성공.")
        return summary_dict
    except json.JSONDecodeError:
        # ...
        raise ValueError("LM Studio가 반환한 요약이 올바른 JSON 형식이 아닙니다.")