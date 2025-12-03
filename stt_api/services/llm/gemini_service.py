"""
Google Gemini API 서비스
"""

import json
from typing import Dict, Any
import google.generativeai as genai

from .base_llm_service import BaseLLMService, LLMConnectionError, LLMResponseError
from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


class GeminiService(BaseLLMService):
    """Google Gemini API 서비스 구현"""

    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL_NAME
        self.model = None

        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)

    async def check_connection(self) -> bool:
        """Gemini API 연결 확인"""
        try:
            logger.info("Gemini API 연결 확인...")

            if not self.api_key:
                raise LLMConnectionError("GEMINI_API_KEY가 설정되지 않았습니다")

            # 간단한 테스트 요청
            response = self.model.generate_content("Test")

            logger.info("Gemini API 연결 성공", model=self.model_name)
            return True

        except Exception as e:
            logger.error("Gemini API 연결 실패", error=str(e))
            raise LLMConnectionError(f"Gemini 연결 실패: {e}")

    async def get_summary(self, transcript: str) -> Dict[str, Any]:
        """
        Gemini로 텍스트 요약

        Args:
            transcript: 요약할 텍스트

        Returns:
            요약 결과 딕셔너리
        """
        logger.info("Gemini 요약 작업 시작...")

        try:
            prompt = self._build_summary_prompt(transcript)

            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    max_output_tokens=1024,
                )
            )

            # 응답 파싱
            result_text = response.text.strip()

            # JSON 추출
            summary_dict = self._parse_json_response(result_text)

            logger.info("Gemini 요약 완료")
            return summary_dict

        except Exception as e:
            logger.error("Gemini 요약 실패", exc_info=True, error=str(e))
            raise LLMResponseError(f"Gemini 요약 실패: {e}")

    async def get_medical_summary(self, transcript: str) -> Dict[str, Any]:
        """
        Gemini로 의료 대화 요약

        Args:
            transcript: 의료 대화 텍스트

        Returns:
            구조화된 의료 요약
        """
        logger.info("Gemini 의료 요약 작업 시작...")

        try:
            prompt = self._build_medical_summary_prompt(transcript)

            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    max_output_tokens=2048,
                )
            )

            result_text = response.text.strip()
            summary_dict = self._parse_json_response(result_text)

            logger.info("Gemini 의료 요약 완료")
            return summary_dict

        except Exception as e:
            logger.error("Gemini 의료 요약 실패", exc_info=True, error=str(e))
            raise LLMResponseError(f"Gemini 의료 요약 실패: {e}")

    def _build_summary_prompt(self, transcript: str) -> str:
        """간단한 요약 프롬프트"""
        return f"""
                아래 텍스트를 요약해주세요.
                결과는 반드시 JSON 형식으로만 출력하세요.
                
                [텍스트]
                {transcript}
                
                [JSON 형식]
                {{
                    "summary": "요약된 내용"
                }}
                
                [지침]
                1. 순수 JSON만 출력하세요 (마크다운 불필요)
                2. 간결하고 명확하게 작성하세요
                """

    def _build_medical_summary_prompt(self, transcript: str) -> str:
        """의료 대화 요약 프롬프트"""
        return f"""
                당신은 의료 대화를 분석하는 전문 AI입니다.
                다음 대화 내용을 분석하여 요약해주세요.
                
                [대화록]
                {transcript}
                
                [JSON 형식]
                {{
                    "main_complaint": "환자의 주요 증상",
                    "diagnosis": "의사의 진단",
                    "recommendation": "처방 및 권고사항"
                }}
                
                [지침]
                1. 순수 JSON만 출력하세요
                2. 각 항목은 1-2문장으로 간결하게
                """

    def _parse_json_response(self, raw_response: str) -> Dict[str, Any]:
        """JSON 응답 파싱"""
        import re

        if not raw_response:
            raise ValueError("Gemini가 비어있는 응답을 반환했습니다")

        try:
            # 마크다운 제거
            cleaned = re.sub(r'```json\s*|\s*```', '', raw_response)
            cleaned = cleaned.strip()

            # JSON 객체 추출
            match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', cleaned, re.DOTALL)

            if not match:
                raise ValueError("JSON 객체를 찾을 수 없습니다")

            json_string = match.group(0).strip()
            parsed_data = json.loads(json_string)

            logger.info("JSON 파싱 성공", keys=list(parsed_data.keys()))
            return parsed_data

        except json.JSONDecodeError as e:
            logger.error("JSON 디코딩 실패", error=str(e))
            raise ValueError("Gemini 응답이 올바른 JSON 형식이 아닙니다")


# 전역 인스턴스
gemini_service = GeminiService()