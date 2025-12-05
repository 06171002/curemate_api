from enum import Enum
from typing import Dict, Any, Optional, List
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

    # ==================== room 관련 메서드 ====================
    async def check_member_exists(
            self,
            room_id: str,
            member_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        특정 방에 해당 참가자가 이미 작업을 시작했는지 확인

        Returns:
            기존 작업 정보 또는 None
        """
        try:
            return await self.db.check_member_exists(room_id, member_id)
        except Exception as e:
            logger.error(
                "참가자 존재 확인 실패",
                room_id=room_id,
                member_id=member_id,
                error=str(e)
            )
            return None

    async def get_or_create_room(
            self,
            room_id: str
    ) -> Dict[str, Any]:
        """
        방 생성 또는 조회 (JobManager를 통한 통합 인터페이스)

        Returns:
            방 정보 딕셔너리
        """
        try:
            room_info = await self.db.create_or_get_room(room_id)

            logger.info(
                "방 준비 완료",
                room_id=room_id,
                room_seq=room_info.get("room_seq"),
                is_new=room_info.get("status") == "ACTIVE"
            )

            return room_info

        except Exception as e:
            logger.error(
                "방 생성/조회 실패",
                room_id=room_id,
                error=str(e)
            )
            raise StorageException(
                message="방 생성/조회 중 오류 발생",
                details={"room_id": room_id, "error": str(e)}
            )

    async def get_room_info(self, room_id: str) -> Optional[Dict[str, Any]]:
        """
        방 상세 정보 조회 (참가자 목록 포함)
        """
        try:
            return await self.db.get_room_info_with_members(room_id)
        except Exception as e:
            logger.error(
                "방 정보 조회 실패",
                room_id=room_id,
                error=str(e)
            )
            return None

    async def create_job_with_room(
            self,
            job_id: str,
            job_type: JobType,
            room_id: str,
            member_id: str,
            metadata: Dict[str, Any] = None
    ) -> bool:
        """
        화상 회의용 작업 생성 (room_id, member_id 포함)

        이 메서드는 create_job을 대체하여 화상 회의 전용 로직 처리
        """
        try:
            # DB에 작업 생성 (room_id, member_id 포함)
            await self.db.create_stt_job(
                job_id,
                job_type.value,
                metadata=metadata,
                room_id=room_id,
                member_id=member_id
            )

            # Redis 캐시 생성
            try:
                cache_metadata = metadata.copy() if metadata else {}
                cache_metadata.update({
                    "room_id": room_id,
                    "member_id": member_id
                })
                self.cache.create_job(job_id, cache_metadata)
            except Exception as cache_error:
                logger.warning(
                    "Redis 캐시 생성 실패",
                    job_id=job_id,
                    error=str(cache_error)
                )

            logger.info(
                "화상 회의 작업 생성 완료",
                job_id=job_id,
                room_id=room_id,
                member_id=member_id,
                job_type=job_type.value
            )

            return True

        except Exception as e:
            logger.error(
                "화상 회의 작업 생성 실패",
                job_id=job_id,
                room_id=room_id,
                member_id=member_id,
                exc_info=True
            )
            raise JobCreationError(job_id=job_id, reason=str(e))

    async def get_room_job_status_summary(
            self,
            room_id: str
    ) -> Dict[str, int]:
        """방의 모든 작업 상태 집계"""
        return await self.db.get_room_job_status_summary(room_id)

    async def is_room_ready_for_summary(self, room_id: str) -> bool:
        """방이 통합 요약 가능한 상태인지 확인"""
        return await self.db.is_room_ready_for_summary(room_id)

    async def check_and_trigger_room_summary(self, room_id: str) -> bool:
        """
        ✅ 개선: 방의 모든 작업이 완료되었는지 확인 후 요약 트리거

        Args:
            room_id: 방 ID

        Returns:
            True: 요약 트리거됨
            False: 아직 진행 중인 작업 있음
        """
        try:
            # ✅ 방의 모든 작업이 완료되었는지 확인
            is_ready = await self.is_room_ready_for_summary(room_id)

            if not is_ready:
                # 상태 로그
                status_summary = await self.get_room_job_status_summary(room_id)

                logger.info(
                    "방 요약 대기 중 (아직 진행 중인 작업 있음)",
                    room_id=room_id,
                    status_summary=status_summary
                )
                return False

            # ✅ 모든 작업 완료! 요약 트리거
            logger.info(
                "방의 모든 작업 완료 감지, 통합 요약 트리거",
                room_id=room_id
            )

            # 백그라운드 Task로 요약 생성
            from stt_api.services.tasks import generate_room_summary_task
            generate_room_summary_task.delay(room_id)

            return True

        except Exception as e:
            logger.error(
                "요약 트리거 확인 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            return False

    async def get_completed_room_transcripts(
            self,
            room_id: str
    ) -> List[Dict[str, Any]]:
        """
        방의 완료된 모든 대화록 조회 (통합 요약용)
        """
        return await self.db.get_completed_room_transcripts(room_id)

    async def update_room_summary(
            self,
            room_id: str,
            summary: Dict[str, Any]
    ) -> bool:
        """
        방의 통합 요약 업데이트
        """
        return await self.db.update_room_summary(room_id, summary)


# ==================== 전역 인스턴스 ====================

job_manager = JobManager()