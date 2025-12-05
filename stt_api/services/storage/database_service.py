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
from stt_api.models.database_models import STTJob, STTSegment, STTErrorLog, STTRoom
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
        metadata: Dict = None,
        room_id: str = None,  # ✅ 추가
        member_id: str = None,  # ✅ 추가
    ) -> bool:
        """
        T_STT_JOB 생성

        Args:
            - REALTIME 모드: room_id, member_id 필수
        """
        try:
            async with get_transaction() as session:
                job = STTJob(
                    job_id=job_id,
                    job_type=job_type,
                    status="PENDING",
                    room_id=room_id,  # ✅ 추가
                    member_id=member_id,  # ✅ 추가
                    job_metadata=metadata or {},  # ✅ 수정: metadata → job_metadata
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

    async def get_room_jobs(
            self,
            room_id: str,
            status: str = None
    ) -> List[Dict[str, Any]]:
        """
        특정 방의 모든 작업 조회

        Args:
            room_id: 방 ID
            status: 상태 필터 (None이면 전체)

        Returns:
            작업 목록
        """
        try:
            async with get_transaction() as session:
                stmt = select(STTJob).where(STTJob.room_id == room_id)

                if status:
                    stmt = stmt.where(STTJob.status == status)

                stmt = stmt.order_by(STTJob.reg_dttm)

                result = await session.execute(stmt)
                jobs = result.scalars().all()

            logger.debug(
                "방 작업 목록 조회",
                room_id=room_id,
                count=len(jobs)
            )

            return [job.to_dict() for job in jobs]

        except Exception as e:
            logger.error(
                "방 작업 조회 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            return []

    async def get_completed_room_transcripts(
            self,
            room_id: str
    ) -> List[Dict[str, Any]]:
        """
        방의 완료된 모든 대화록 조회 (통합 요약용)

        Returns:
            [{"member_id": "alice", "transcript": "...", "summary": {...}}, ...]
        """
        try:
            async with get_transaction() as session:
                stmt = (
                    select(STTJob)
                    .where(STTJob.room_id == room_id)
                    .where(STTJob.status.in_(["COMPLETED", "TRANSCRIBED"]))
                    .where(STTJob.original_transcript.isnot(None))
                    .order_by(STTJob.reg_dttm)
                )

                result = await session.execute(stmt)
                jobs = result.scalars().all()

            transcripts = []
            for job in jobs:
                transcripts.append({
                    "job_id": job.job_id,
                    "member_id": job.member_id,
                    "transcript": job.original_transcript,
                    "summary": job.structured_summary,
                    "reg_dttm": job.reg_dttm.isoformat() if job.reg_dttm else None
                })

            logger.info(
                "방 대화록 조회",
                room_id=room_id,
                transcript_count=len(transcripts)
            )

            return transcripts

        except Exception as e:
            logger.error(
                "방 대화록 조회 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            return []

    async def create_or_get_room(self, room_id: str) -> Dict[str, Any]:
        """
        방 생성 또는 조회

        Returns:
            방 정보 딕셔너리
        """
        try:
            async with get_transaction() as session:
                # 기존 방 조회
                stmt = select(STTRoom).where(STTRoom.room_id == room_id)
                result = await session.execute(stmt)
                room = result.scalar_one_or_none()

                if room:
                    logger.debug("기존 방 조회", room_id=room_id)
                    return room.to_dict()

                # 새 방 생성
                room = STTRoom(
                    room_id=room_id,
                    status="ACTIVE",
                    reg_id="system"
                )
                session.add(room)
                await session.flush()  # room_seq 얻기 위해

                await session.refresh(room)

                logger.info("새 방 생성", room_id=room_id, room_seq=room.room_seq)
                return room.to_dict()

        except Exception as e:
            logger.error("방 생성/조회 실패", exc_info=True, error=str(e))
            raise StorageException(
                message="방 생성/조회 중 오류 발생",
                details={"room_id": room_id, "error": str(e)}
            )

    async def check_member_exists(
            self,
            room_id: str,
            member_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        특정 방에 해당 참가자가 이미 있는지 확인

        Args:
            room_id: 방 ID
            member_id: 참가자 ID

        Returns:
            기존 작업 정보 (없으면 None)
        """
        try:
            async with get_transaction() as session:
                stmt = (
                    select(STTJob)
                    .where(
                        and_(
                            STTJob.room_id == room_id,
                            STTJob.member_id == member_id
                        )
                    )
                    .order_by(STTJob.reg_dttm.desc())  # 가장 최근 작업
                    .limit(1)
                )

                result = await session.execute(stmt)
                job = result.scalar_one_or_none()

                if job:
                    logger.info(
                        "기존 참가자 발견",
                        room_id=room_id,
                        member_id=member_id,
                        existing_job_id=job.job_id,
                        status=job.status
                    )
                    return job.to_dict()

                return None

        except Exception as e:
            logger.error(
                "참가자 존재 확인 실패",
                exc_info=True,
                room_id=room_id,
                member_id=member_id,
                error=str(e)
            )
            return None

    async def get_room_member_count(self, room_id: str) -> int:
        """
        방의 고유 참가자 수 조회

        Returns:
            고유 member_id 개수
        """
        try:
            async with get_transaction() as session:
                from sqlalchemy import func, distinct

                stmt = (
                    select(func.count(distinct(STTJob.member_id)))
                    .where(STTJob.room_id == room_id)
                )

                result = await session.execute(stmt)
                count = result.scalar()

                logger.debug(
                    "방 참가자 수 조회",
                    room_id=room_id,
                    member_count=count
                )

                return count or 0

        except Exception as e:
            logger.error(
                "참가자 수 조회 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            return 0

    async def get_room_info_with_members(
            self,
            room_id: str
    ) -> Dict[str, Any]:
        """
        방 정보 + 참가자 목록 조회

        Returns:
            {
                "room_id": "...",
                "status": "...",
                "member_count": 3,
                "members": [
                    {"member_id": "alice", "job_count": 2, "last_active": "..."},
                    {"member_id": "bob", "job_count": 1, "last_active": "..."}
                ]
            }
        """
        try:
            async with get_transaction() as session:
                # 1. 방 정보 조회
                room_stmt = select(STTRoom).where(STTRoom.room_id == room_id)
                room_result = await session.execute(room_stmt)
                room = room_result.scalar_one_or_none()

                if not room:
                    return None

                # 2. 참가자별 작업 통계 조회
                from sqlalchemy import func

                member_stmt = (
                    select(
                        STTJob.member_id,
                        func.count(STTJob.job_id).label('job_count'),
                        func.max(STTJob.upd_dttm).label('last_active')
                    )
                    .where(STTJob.room_id == room_id)
                    .group_by(STTJob.member_id)
                    .order_by(func.max(STTJob.upd_dttm).desc())
                )

                member_result = await session.execute(member_stmt)
                members = []

                for row in member_result:
                    members.append({
                        "member_id": row.member_id,
                        "job_count": row.job_count,
                        "last_active": row.last_active.isoformat() if row.last_active else None
                    })

                return {
                    "room_seq": room.room_seq,
                    "room_id": room.room_id,
                    "status": room.status,
                    "member_count": len(members),
                    "members": members,
                    "total_summary": room.total_summary,
                    "reg_dttm": room.reg_dttm.isoformat() if room.reg_dttm else None
                }

        except Exception as e:
            logger.error(
                "방 상세 정보 조회 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            return None

    async def get_room_job_status_summary(
            self,
            room_id: str
    ) -> Dict[str, int]:
        """
        방의 모든 작업 상태 집계

        Returns:
            {
                "total": 5,
                "pending": 0,
                "processing": 0,
                "transcribed": 2,
                "completed": 3,
                "failed": 0
            }
        """
        try:
            async with get_transaction() as session:
                from sqlalchemy import func, case

                # 상태별 카운트 쿼리
                stmt = (
                    select(
                        func.count().label('total'),
                        func.sum(
                            case((STTJob.status == "PENDING", 1), else_=0)
                        ).label('pending'),
                        func.sum(
                            case((STTJob.status == "PROCESSING", 1), else_=0)
                        ).label('processing'),
                        func.sum(
                            case((STTJob.status == "TRANSCRIBED", 1), else_=0)
                        ).label('transcribed'),
                        func.sum(
                            case((STTJob.status == "COMPLETED", 1), else_=0)
                        ).label('completed'),
                        func.sum(
                            case((STTJob.status == "FAILED", 1), else_=0)
                        ).label('failed'),
                    )
                    .where(STTJob.room_id == room_id)
                )

                result = await session.execute(stmt)
                row = result.one()

                summary = {
                    "total": row.total or 0,
                    "pending": row.pending or 0,
                    "processing": row.processing or 0,
                    "transcribed": row.transcribed or 0,
                    "completed": row.completed or 0,
                    "failed": row.failed or 0
                }

                logger.debug(
                    "방 작업 상태 집계",
                    room_id=room_id,
                    summary=summary
                )

                return summary

        except Exception as e:
            logger.error(
                "방 작업 상태 집계 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            return {"total": 0}

    async def is_room_ready_for_summary(self, room_id: str) -> bool:
        """
        방이 통합 요약 가능한 상태인지 확인

        조건:
        1. 최소 1개 이상의 작업 존재
        2. 모든 작업이 TRANSCRIBED 또는 COMPLETED 상태
        3. PENDING/PROCESSING 상태 작업 없음

        Returns:
            True: 요약 가능
            False: 아직 진행 중인 작업 있음
        """
        try:
            summary = await self.get_room_job_status_summary(room_id)

            total = summary["total"]
            pending = summary["pending"]
            processing = summary["processing"]
            ready = summary["transcribed"] + summary["completed"]

            # 조건 확인
            is_ready = (
                    total > 0 and  # 작업이 하나 이상 있어야 함
                    pending == 0 and  # PENDING 없음
                    processing == 0 and  # PROCESSING 없음
                    ready == total  # 모든 작업이 완료됨
            )

            logger.info(
                "방 요약 준비 상태 확인",
                room_id=room_id,
                is_ready=is_ready,
                total=total,
                pending=pending,
                processing=processing,
                ready=ready
            )

            return is_ready

        except Exception as e:
            logger.error(
                "방 요약 준비 상태 확인 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            return False

    async def update_room_summary(
            self,
            room_id: str,
            summary: Dict[str, Any]
    ) -> bool:
        """
        방의 통합 요약 업데이트

        Args:
            room_id: 방 ID
            summary: 통합 요약 JSON

        Returns:
            성공 여부
        """
        try:
            async with get_transaction() as session:
                stmt = (
                    update(STTRoom)
                    .where(STTRoom.room_id == room_id)
                    .values(
                        total_summary=summary,
                        upd_dttm=datetime.now(),
                        upd_id="system"
                    )
                )

                result = await session.execute(stmt)

                if result.rowcount == 0:
                    logger.warning("업데이트할 방을 찾을 수 없음", room_id=room_id)
                    return False

                logger.info("방 통합 요약 DB 저장 완료", room_id=room_id)
                return True

        except Exception as e:
            logger.error(
                "방 통합 요약 저장 실패",
                exc_info=True,
                room_id=room_id,
                error=str(e)
            )
            return False

# 전역 인스턴스 생성
db_service = DatabaseService()