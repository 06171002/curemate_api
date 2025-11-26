"""
데이터베이스 서비스 (실제 DB 연동 버전)

SQLAlchemy를 사용한 MariaDB 연동
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import json
from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from stt_api.core.database import get_transaction
from stt_api.models.database_models import STTJob, STTSegment, STTErrorLog
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import (
    StorageException,
    JobNotFoundException,
    JobCreationError
)

logger = get_logger(__name__)


class DatabaseService:
    """
    데이터베이스 서비스 (실제 DB 연동)

    비동기 SQLAlchemy를 사용한 MariaDB 작업 처리
    """

    def __init__(self):
        self.db_type = "mariadb"
        logger.info("DB 서비스 초기화", db_type=self.db_type)

    # ==================== T_STT_JOB 관련 ====================

    async def create_stt_job(
            self,
            job_id: str,
            job_type: str,
            metadata: Dict = None
    ) -> bool:
        """
        T_STT_JOB 테이블에 새로운 작업 생성
        """
        try:
            async with get_transaction() as session:
                job = STTJob(
                    job_id=job_id,
                    job_type=job_type,
                    status="PENDING",
                    metadata=metadata or {},
                    reg_id="system"  # TODO: 실제 사용자 ID로 변경
                )

                session.add(job)
                # commit은 get_transaction에서 자동 처리

            logger.info(
                "T_STT_JOB 레코드 생성",
                job_id=job_id,
                job_type=job_type
            )
            return True

        except Exception as e:
            logger.error(
                "T_STT_JOB 생성 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            raise JobCreationError(job_id=job_id, reason=str(e))

    async def get_stt_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        T_STT_JOB 테이블에서 작업 조회
        """
        try:
            async with get_transaction() as session:
                stmt = select(STTJob).where(STTJob.job_id == job_id)
                result = await session.execute(stmt)
                job = result.scalar_one_or_none()

                if not job:
                    raise JobNotFoundException(job_id=job_id)

                logger.debug("T_STT_JOB 조회", job_id=job_id)
                return job.to_dict()

        except JobNotFoundException:
            raise
        except Exception as e:
            logger.error(
                "T_STT_JOB 조회 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            raise StorageException(
                message=f"작업 조회 중 오류 발생",
                details={"job_id": job_id, "error": str(e)}
            )

    async def update_stt_job_status(
            self,
            job_id: str,
            status: str,
            transcript: str = None,
            summary: Dict = None,
            error_message: str = None
    ) -> bool:
        """
        T_STT_JOB 테이블 상태 업데이트
        """
        try:
            async with get_transaction() as session:
                # 업데이트할 데이터 준비
                update_data = {
                    "status": status,
                    "upd_dttm": datetime.now(),
                    "upd_id": "system"  # TODO: 실제 사용자 ID로 변경
                }

                if transcript:
                    update_data["original_transcript"] = transcript
                if summary:
                    update_data["structured_summary"] = summary
                if error_message:
                    update_data["error_message"] = error_message

                # UPDATE 쿼리 실행
                stmt = (
                    update(STTJob)
                    .where(STTJob.job_id == job_id)
                    .values(**update_data)
                )
                result = await session.execute(stmt)

                if result.rowcount == 0:
                    raise JobNotFoundException(job_id=job_id)

            logger.info(
                "T_STT_JOB 업데이트",
                job_id=job_id,
                status=status
            )
            return True

        except JobNotFoundException:
            raise
        except Exception as e:
            logger.error(
                "T_STT_JOB 업데이트 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            raise StorageException(
                message=f"작업 상태 업데이트 실패",
                details={"job_id": job_id, "error": str(e)}
            )

    # ==================== T_STT_SEGMENT 관련 ====================

    async def insert_stt_segment(
            self,
            job_id: str,
            segment_text: str,
            start_time: float = None,
            end_time: float = None
    ) -> bool:
        """
        T_STT_SEGMENT 테이블에 STT 세그먼트 삽입
        """
        try:
            async with get_transaction() as session:
                segment = STTSegment(
                    job_id=job_id,
                    segment_text=segment_text,
                    start_time=start_time,
                    end_time=end_time,
                    reg_id="system"  # TODO: 실제 사용자 ID로 변경
                )

                session.add(segment)

            logger.debug(
                "T_STT_SEGMENT 삽입",
                job_id=job_id,
                text_preview=segment_text[:50]
            )
            return True

        except Exception as e:
            logger.error(
                "T_STT_SEGMENT 삽입 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            return False

    async def get_stt_segments(self, job_id: str) -> List[Dict[str, Any]]:
        """
        특정 job의 모든 세그먼트 조회
        """
        try:
            async with get_transaction() as session:
                stmt = (
                    select(STTSegment)
                    .where(STTSegment.job_id == job_id)
                    .order_by(STTSegment.start_time)
                )
                result = await session.execute(stmt)
                segments = result.scalars().all()

            logger.debug(
                "T_STT_SEGMENT 조회",
                job_id=job_id,
                count=len(segments)
            )

            return [seg.to_dict() for seg in segments]

        except Exception as e:
            logger.error(
                "T_STT_SEGMENT 조회 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            return []

    # ==================== T_STT_ERROR_LOG 관련 ====================

    async def log_error(
            self,
            job_id: str,
            service_name: str,
            error_message: str
    ) -> bool:
        """
        T_STT_ERROR_LOG 테이블에 에러 로그 기록
        """
        try:
            async with get_transaction() as session:
                error_log = STTErrorLog(
                    job_id=job_id,
                    service_name=service_name,
                    error_message=error_message,
                    reg_id="system"  # TODO: 실제 사용자 ID로 변경
                )

                session.add(error_log)

            logger.info(
                "T_STT_ERROR_LOG 기록",
                job_id=job_id,
                service_name=service_name,
                error_preview=error_message[:100]
            )
            return True

        except Exception as e:
            logger.error(
                "T_STT_ERROR_LOG 기록 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            return False

    async def get_error_logs(self, job_id: str) -> List[Dict[str, Any]]:
        """
        특정 job의 모든 에러 로그 조회
        """
        try:
            async with get_transaction() as session:
                stmt = (
                    select(STTErrorLog)
                    .where(STTErrorLog.job_id == job_id)
                    .order_by(STTErrorLog.reg_dttm.desc())
                )
                result = await session.execute(stmt)
                errors = result.scalars().all()

            logger.debug(
                "T_STT_ERROR_LOG 조회",
                job_id=job_id,
                count=len(errors)
            )

            return [err.to_dict() for err in errors]

        except Exception as e:
            logger.error(
                "T_STT_ERROR_LOG 조회 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            return []


# 전역 인스턴스 생성
db_service = DatabaseService()