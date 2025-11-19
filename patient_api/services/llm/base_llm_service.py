from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseLLMService(ABC):
    """
    LLM 서비스 추상 클래스
    모든 LLM 구현체는 이 클래스를 상속받아야 함
    """

    @abstractmethod
    async def check_connection(self) -> bool:
        """
        서버 연결 확인

        Returns:
            연결 성공 여부
        """
        pass

    @abstractmethod
    async def get_summary(self, transcript: str) -> Dict[str, Any]:
        """
        텍스트 요약 생성

        Args:
            transcript: 요약할 텍스트

        Returns:
            요약 결과 딕셔너리
        """
        pass

    @abstractmethod
    async def get_medical_summary(self, transcript: str) -> Dict[str, Any]:
        """
        의료 대화 요약 생성

        Args:
            transcript: 의료 대화 텍스트

        Returns:
            구조화된 의료 요약
        """
        pass


class LLMServiceError(Exception):
    """LLM 서비스 관련 에러"""
    pass


class LLMConnectionError(LLMServiceError):
    """LLM 연결 에러"""
    pass


class LLMResponseError(LLMServiceError):
    """LLM 응답 파싱 에러"""
    pass