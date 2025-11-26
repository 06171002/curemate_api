from enum import Enum
from typing import Dict, Any, Optional
import asyncio
from .database_service import db_service
from . import cache_service
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import (
    StorageException,
    JobNotFoundException,
    JobCreationError
)

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

    async def create_job(
            self,
            job_id: str,
            job_type: JobType,
            metadata: Dict[str, Any] = None
    ) -> bool:
        """
        새 작업 생성 (DB + Redis)
        ✅ async 함수로 변경 (await 사용)
        """
        try:
            # ✅ asyncio.run() 제거 -> await 사용
            await self.db.create_stt_job(
                job_id,
                job_type.value,
                metadata=metadata
            )

            try:
                self.cache.create_job(job_id, metadata)
            except Exception as cache_error:
                logger.warning("Redis 캐시 생성 실패", job_id=job_id, error=str(cache_error))

            logger.info(
                "작업 생성 완료",
                job_id=job_id,
                job_type=job_type.value
            )
            return True

        except JobCreationError:
            raise
        except Exception as e:
            logger.error("작업 생성 중 오류", job_id=job_id, exc_info=True)
            raise JobCreationError(job_id=job_id, reason=str(e))

    # ==================== 작업 조회 ====================

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        작업 조회 (Redis → DB 순서로 폴백)
        ✅ async 함수로 변경
        """
        try:
            # Redis 캐시 조회 (동기 함수지만 async 안에서 호출 가능)
            try:
                cached_job = self.cache.get_job(job_id)
                if cached_job:
                    logger.debug("캐시 히트", job_id=job_id)
                    return cached_job
            except Exception as cache_error:
                logger.warning("캐시 조회 실패, DB로 폴백", job_id=job_id)

            # ✅ DB 조회 (await 사용)
            logger.debug("캐시 미스, DB 조회", job_id=job_id)
            db_job = await self.db.get_stt_job(job_id)

            if not db_job:
                raise JobNotFoundException(job_id=job_id)

            # DB 데이터를 Redis에 캐싱
            try:
                self._update_cache_from_db(job_id, db_job)
            except Exception:
                pass

            return db_job

        except JobNotFoundException:
            raise
        except Exception as e:
            logger.error("작업 조회 실패", job_id=job_id, exc_info=True)
            raise StorageException(
                message=f"작업 조회 중 오류",
                details={"job_id": job_id, "error": str(e)}
            )

    # ==================== 작업 상태 업데이트 ====================

    async def update_status(
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
        ✅ async 함수로 변경
        """
        try:
            # ✅ DB 업데이트 (await 사용)
            await self.db.update_stt_job_status(
                job_id,
                status.value,
                transcript=transcript,
                summary=summary,
                error_message=error_message
            )

            # Redis 캐시 업데이트
            cache_data = {
                "status": status.value.lower(),
                **extra_data
            }

            if transcript:
                cache_data["original_transcript"] = transcript
            if summary:
                cache_data["structured_summary"] = summary
            if error_message:
                cache_data["error_message"] = error_message

            try:
                self.cache.update_job(job_id, cache_data)
            except Exception as cache_error:
                logger.warning("Redis 캐시 업데이트 실패", job_id=job_id)

            logger.info("상태 업데이트", job_id=job_id, job_status=status.value)
            return True

        except StorageException:
            raise
        except Exception as e:
            logger.error("상태 업데이트 중 오류", job_id=job_id, exc_info=True)
            raise StorageException(
                message=f"상태 업데이트 실패",
                details={"job_id": job_id, "error": str(e)}
            )

    # ==================== Pub/Sub (Redis 전용) ====================

    def publish_event(self, job_id: str, event_data: Dict[str, Any]) -> None:
        """작업 이벤트 발행 (Redis Pub/Sub) - 동기 유지 가능"""
        try:
            self.cache.publish_message(job_id, event_data)
            logger.info("이벤트 발행", job_id=job_id, event_data=event_data)
        except Exception as e:
            logger.error("이벤트 발행 실패", message=str(e))

    async def subscribe_events(self, job_id: str):
        """작업 이벤트 구독 (Redis Pub/Sub)"""
        try:
            async for message in self.cache.subscribe_to_messages(job_id):
                yield message
        except Exception as e:
            logger.error("이벤트 구독 오류", message=str(e))

    # ==================== 세그먼트 관리 ====================

    async def save_segment(
            self,
            job_id: str,
            segment_text: str,
            start_time: float = None,
            end_time: float = None
    ) -> bool:
        """STT 세그먼트 저장 (DB) - async 변경"""
        try:
            await self.db.insert_stt_segment(
                job_id,
                segment_text,
                start_time,
                end_time
            )
            return True
        except Exception as e:
            logger.error("세그먼트 저장 실패", error=str(e))
            return False

    async def get_segments(self, job_id: str):
        """작업의 모든 세그먼트 조회 - async 변경"""
        try:
            return await self.db.get_stt_segments(job_id)
        except Exception as e:
            logger.error("세그먼트 조회 실패", error=str(e))
            return []

    # ==================== 에러 로그 ====================

    async def log_error(self, job_id: str, service_name: str, error_message: str) -> bool:
        """에러 로그 기록 - async 변경"""
        try:
            await self.db.log_error(job_id, service_name, error_message)
            return True
        except Exception as e:
            logger.error("에러 로그 기록 실패", error=str(e))
            return False

    async def get_errors(self, job_id: str):
        """작업의 모든 에러 로그 조회 - async 변경"""
        try:
            return await self.db.get_error_logs(job_id)
        except Exception as e:
            logger.error("에러 로그 조회 실패", error=str(e))
            return []

    # ==================== 내부 헬퍼 메서드 ====================

    def _update_cache_from_db(self, job_id: str, db_data: Dict[str, Any]) -> None:
        """DB 데이터를 Redis 캐시에 동기화 (내부용)"""
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
            logger.error("캐시 동기화 실패", error=str(e))


# ==================== 전역 인스턴스 ====================

job_manager = JobManager()