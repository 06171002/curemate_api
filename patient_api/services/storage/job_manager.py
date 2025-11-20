# patient_api/services/storage/job_manager.py

from enum import Enum
from typing import Dict, Any, Optional
import sys
from .database_service import db_service
from . import cache_service
from patient_api.core.logging_config import get_logger

logger = get_logger(__name__)


class JobStatus(Enum):
    """작업 상태 열거형"""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    TRANSCRIBED = "TRANSCRIBED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobType(Enum):
    """작업 타입 열거형"""
    BATCH = "BATCH"
    REALTIME = "REALTIME"


class JobManager:
    """
    작업 생명주기를 통합 관리하는 클래스
    - DB를 주 저장소로 사용
    - Redis를 빠른 조회/Pub-Sub을 위한 캐시로 사용
    """

    def __init__(self):
        self.db = db_service
        self.cache = cache_service
        logger.info("JobManager 초기화 완료")

    # ==================== 작업 생성 ====================

    def create_job(
            self,
            job_id: str,
            job_type: JobType,
            metadata: Dict[str, Any] = None
    ) -> bool:
        """
        새 작업 생성 (DB + Redis)

        Args:
            job_id: 작업 고유 ID
            job_type: BATCH 또는 REALTIME
            metadata: 추가 메타데이터

        Returns:
            성공 여부
        """
        try:
            # 1. DB에 작업 생성 (Primary)
            db_success = self.db.create_stt_job(
                job_id,
                job_type.value,
                metadata=metadata
            )

            if not db_success:
                logger.error("DB 작업 생성 실패", job_id=job_id)
                return False

            # 2. Redis 캐시 생성 (Secondary, 실패해도 치명적이지 않음)
            cache_success = self.cache.create_job(job_id, metadata)

            if not cache_success:
                logger.warning("Redis 캐시 생성 실패", job_id=job_id)
                self.db.log_error(job_id, "job_manager", "Redis 캐시 생성 실패")

            logger.info(
                "작업 생성 완료",
                job_id=job_id,
                job_type=job_type.value
            )
            return True

        except Exception as e:
            logger.error(
                "작업 생성 중 오류",
                job_id=job_id,
                exc_info=True,
                error=str(e)
            )
            self.db.log_error(job_id, "job_manager", str(e))
            return False

    # ==================== 작업 조회 ====================

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        작업 조회 (Redis → DB 순서로 폴백)

        Returns:
            작업 데이터 또는 None
        """
        try:
            # 1. Redis 캐시에서 먼저 조회 (빠름)
            cached_job = self.cache.get_job(job_id)
            if cached_job:
                logger.info("캐시 히트", job_id=job_id)
                return cached_job

            # 2. DB에서 조회 (폴백)
            logger.info("캐시 미스, DB 조회", job_id=job_id)
            db_job = self.db.get_stt_job(job_id)

            if db_job:
                # DB에서 가져온 데이터를 Redis에 캐싱 (다음 조회 최적화)
                self._update_cache_from_db(job_id, db_job)

            return db_job

        except Exception as e:
            logger.error("작업 조회 실패",job_id=job_id,exc_info=True,message=sys.stderr)
            return None

    # ==================== 작업 상태 업데이트 ====================

    def update_status(
            self,
            job_id: str,
            status: JobStatus,
            transcript: str = None,
            summary: Dict = None,
            error_message: str = None,
            **extra_data
    ) -> bool:
        """
        작업 상태 업데이트 (DB + Redis 동기화)

        Args:
            job_id: 작업 ID
            status: 새 상태
            transcript: STT 결과
            summary: 요약 결과
            error_message: 에러 메시지
            **extra_data: 추가 데이터 (segment_count 등)

        Returns:
            성공 여부
        """
        try:
            # 1. DB 업데이트 (Primary, 필수)
            db_success = self.db.update_stt_job_status(
                job_id,
                status.value,
                transcript=transcript,
                summary=summary,
                error_message=error_message
            )

            if not db_success:
                logger.error("DB 상태 업데이트 실패", job_id=job_id,exc_info=True,message=sys.stderr)
                return False

            # 2. Redis 캐시 업데이트 (Secondary, 선택적)
            cache_data = {
                "status": status.value.lower(),  # Redis는 소문자 사용
                **extra_data
            }

            if transcript:
                cache_data["original_transcript"] = transcript
            if summary:
                cache_data["structured_summary"] = summary
            if error_message:
                cache_data["error_message"] = error_message

            cache_success = self.cache.update_job(job_id, cache_data)

            if not cache_success:
                logger.warning("Redis 캐시 업데이트 실패", job_id=job_id)
                # Redis 실패는 치명적이지 않으므로 경고만

            logger.info("상태 업데이트", job_id=job_id, job_status= status.value)
            return True

        except Exception as e:
            logger.error("상태 업데이트 중 오류", job_id=job_id, message=sys.stderr)
            self.db.log_error(job_id, "job_manager", str(e))
            return False

    # ==================== Pub/Sub (Redis 전용) ====================

    def publish_event(self, job_id: str, event_data: Dict[str, Any]) -> None:
        """
        작업 이벤트 발행 (Redis Pub/Sub)

        Args:
            job_id: 작업 ID
            event_data: 이벤트 데이터 (type, text, summary 등)
        """
        try:
            self.cache.publish_message(job_id, event_data)
            logger.info("이벤트 발행", job_id=job_id, event_data= event_data)
        except Exception as e:
            logger.error("이벤트 발행 실패", message=str(e))
            self.db.log_error(job_id, "job_manager_pubsub", str(e))

    async def subscribe_events(self, job_id: str):
        """
        작업 이벤트 구독 (Redis Pub/Sub)

        Yields:
            이벤트 데이터
        """
        try:
            async for message in self.cache.subscribe_to_messages(job_id):
                yield message
        except Exception as e:
            logger.error("이벤트 구독 오류", message=sys.stderr)
            self.db.log_error(job_id, "job_manager_pubsub", str(e))

    # ==================== 세그먼트 관리 (선택적) ====================

    def save_segment(
            self,
            job_id: str,
            segment_text: str,
            start_time: float = None,
            end_time: float = None
    ) -> bool:
        """
        STT 세그먼트 저장 (DB)

        Returns:
            성공 여부
        """
        try:
            return self.db.insert_stt_segment(
                job_id,
                segment_text,
                start_time,
                end_time
            )
        except Exception as e:
            logger.error("세그먼트 저장 실패", message=sys.stderr)
            return False

    def get_segments(self, job_id: str):
        """
        작업의 모든 세그먼트 조회

        Returns:
            세그먼트 리스트
        """
        try:
            return self.db.get_stt_segments(job_id)
        except Exception as e:
            logger.error("세그먼트 조회 실패", error_message= e, message=sys.stderr)
            return []

    # ==================== 에러 로그 ====================

    def log_error(self, job_id: str, service_name: str, error_message: str) -> bool:
        """
        에러 로그 기록

        Returns:
            성공 여부
        """
        try:
            return self.db.log_error(job_id, service_name, error_message)
        except Exception as e:
            logger.error("에러 로그 기록 실패", error_message=e, message=sys.stderr)
            return False

    def get_errors(self, job_id: str):
        """
        작업의 모든 에러 로그 조회

        Returns:
            에러 로그 리스트
        """
        try:
            return self.db.get_error_logs(job_id)
        except Exception as e:
            logger.error("에러 로그 조회 실패", error_message=e, message=sys.stderr)
            return []

    # ==================== 내부 헬퍼 메서드 ====================

    def _update_cache_from_db(self, job_id: str, db_data: Dict[str, Any]) -> None:
        """
        DB 데이터를 Redis 캐시에 동기화 (내부용)
        """
        try:
            cache_data = {
                "status": db_data.get("status", "").lower(),
                "original_transcript": db_data.get("original_transcript"),
                "structured_summary": db_data.get("structured_summary"),
                "error_message": db_data.get("error_message"),
                "metadata": db_data.get("metadata", {})
            }

            self.cache.update_job(job_id, cache_data)
            logger.info("캐시 동기화 완료", job_id=job_id)

        except Exception as e:
            logger.error("캐시 동기화 실패", message=e)


# ==================== 전역 인스턴스 ====================

job_manager = JobManager()