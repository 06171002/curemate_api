"""
SQLAlchemy 데이터베이스 모델 정의

기존 MariaDB 테이블과 매핑되는 ORM 모델들
"""

from sqlalchemy import (
    Column, BigInteger, String, Text, DateTime,
    Float, Boolean, Integer, Index, JSON
)
from sqlalchemy.sql import func
from datetime import datetime

from stt_api.core.database import Base


class STTJob(Base):
    """
    T_STT_JOB 테이블 매핑

    STT 작업 및 요약 상태 관리
    """
    __tablename__ = "t_stt_job"

    job_seq = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="작업 고유 번호"
    )
    job_id = Column(
        String(36),
        unique=True,
        nullable=False,
        index=True,
        comment="작업 고유 ID (UUID)"
    )
    job_type = Column(
        String(20),
        nullable=False,
        index=True,
        comment="작업 유형 (BATCH/REALTIME)"
    )
    status = Column(
        String(20),
        nullable=False,
        default="PENDING",
        index=True,
        comment="작업 상태"
    )

    # ✅ WebRTC 화상 회의용 필드
    room_id = Column(
        String(50),
        index=True,
        comment="화상 회의 방 ID (NULL이면 1:1 모드)"
    )
    member_id = Column(
        String(50),
        comment="참가자 ID"
    )
    original_transcript = Column(
        Text,
        comment="전체 STT 대화록"
    )
    structured_summary = Column(
        JSON,
        comment="LLM 요약 결과 (JSON)"
    )
    error_message = Column(
        Text,
        comment="최종 에러 메시지"
    )
    # ✅ 수정: metadata → job_metadata (SQLAlchemy 예약어 충돌 회피)
    job_metadata = Column(
        "metadata",  # DB 컬럼명은 "metadata" 유지
        JSON,
        comment="파일 정보 등 메타데이터"
    )
    reg_id = Column(
        String(100),
        comment="등록 ID"
    )
    reg_dttm = Column(
        DateTime,
        default=func.now(),
        nullable=False,
        comment="생성 일시"
    )
    upd_id = Column(
        String(100),
        comment="수정 ID"
    )
    upd_dttm = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="수정 일시"
    )

    # 인덱스 정의
    __table_args__ = (
        Index('idx_job_type_status', 'job_type', 'status'),
        Index('idx_room_member', 'room_id', 'member_id'),
        {'comment': 'STT 작업 및 요약 상태 관리'}
    )

    def to_dict(self):
        """모델을 딕셔너리로 변환"""
        return {
            "job_seq": self.job_seq,
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status,
            "room_id": self.room_id,  # ✅ 추가
            "member_id": self.member_id,  # ✅ 추가
            "original_transcript": self.original_transcript,
            "structured_summary": self.structured_summary,
            "error_message": self.error_message,
            "metadata": self.job_metadata,  # ✅ 외부에는 metadata로 노출
            "reg_id": self.reg_id,
            "reg_dttm": self.reg_dttm.isoformat() if self.reg_dttm else None,
            "upd_id": self.upd_id,
            "upd_dttm": self.upd_dttm.isoformat() if self.upd_dttm else None,
        }


class STTSegment(Base):
    """
    T_STT_SEGMENT 테이블 매핑

    실시간 STT 대화 조각 저장
    """
    __tablename__ = "t_stt_segment"

    segment_seq = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="세그먼트 고유 번호"
    )
    job_id = Column(
        String(36),
        nullable=False,
        index=True,
        comment="작업 ID"
    )
    segment_text = Column(
        Text,
        nullable=False,
        comment="인식된 문장(세그먼트)"
    )
    start_time = Column(
        Float,
        comment="오디오 시작 시간 (초)"
    )
    end_time = Column(
        Float,
        comment="오디오 종료 시간 (초)"
    )
    reg_id = Column(
        String(100),
        comment="등록 ID"
    )
    reg_dttm = Column(
        DateTime,
        default=func.now(),
        nullable=False,
        comment="기록 일시"
    )
    upd_id = Column(
        String(100),
        comment="수정 ID"
    )
    upd_dttm = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="수정 일시"
    )

    # 인덱스 정의
    __table_args__ = (
        Index('idx_job_id', 'job_id'),
        Index('idx_start_time', 'start_time'),
        {'comment': '실시간 STT 대화 조각 저장 테이블'}
    )

    def to_dict(self):
        """모델을 딕셔너리로 변환"""
        return {
            "segment_seq": self.segment_seq,
            "job_id": self.job_id,
            "segment_text": self.segment_text,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "reg_id": self.reg_id,
            "reg_dttm": self.reg_dttm.isoformat() if self.reg_dttm else None,
            "upd_id": self.upd_id,
            "upd_dttm": self.upd_dttm.isoformat() if self.upd_dttm else None,
        }


class STTErrorLog(Base):
    """
    T_STT_ERROR_LOG 테이블 매핑

    STT 서비스 에러 로그
    """
    __tablename__ = "t_stt_error_log"

    log_seq = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="로그 고유 번호"
    )
    job_id = Column(
        String(36),
        nullable=False,
        index=True,
        comment="관련 작업 ID (FK)"
    )
    service_name = Column(
        String(50),
        nullable=False,
        index=True,
        comment="에러 발생 서비스명"
    )
    error_message = Column(
        Text,
        nullable=False,
        comment="에러 상세 내용"
    )
    reg_id = Column(
        String(100),
        comment="등록 ID"
    )
    reg_dttm = Column(
        DateTime,
        default=func.now(),
        nullable=False,
        index=True,
        comment="에러 발생 일시"
    )
    upd_id = Column(
        String(100),
        comment="수정 ID"
    )
    upd_dttm = Column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="수정 일시"
    )

    # 인덱스 정의
    __table_args__ = (
        Index('idx_job_error', 'job_id'),
        Index('idx_service_name', 'service_name'),
        {'comment': 'STT 서비스 에러 로그 테이블'}
    )

    def to_dict(self):
        """모델을 딕셔너리로 변환"""
        return {
            "log_seq": self.log_seq,
            "job_id": self.job_id,
            "service_name": self.service_name,
            "error_message": self.error_message,
            "reg_id": self.reg_id,
            "reg_dttm": self.reg_dttm.isoformat() if self.reg_dttm else None,
            "upd_id": self.upd_id,
            "upd_dttm": self.upd_dttm.isoformat() if self.upd_dttm else None,
        }


class STTRoom(Base):
    """
    T_STT_ROOM 테이블 매핑

    화상 회의 방 관리
    """
    __tablename__ = "t_stt_room"

    room_seq = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="방 고유 번호"
    )
    room_id = Column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="화상 회의 방 ID"
    )
    status = Column(
        String(20),
        nullable=False,
        default="ACTIVE",
        comment="방 상태"
    )
    total_summary = Column(
        JSON,
        comment="전체 참가자 통합 요약"
    )
    reg_id = Column(String(100), comment="생성자 ID")
    reg_dttm = Column(DateTime, default=func.now(), nullable=False)
    upd_id = Column(String(100), comment="수정 ID")
    upd_dttm = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        {'comment': 'STT 화상 회의 방(헤더) 관리 테이블'}
    )

    def to_dict(self):
        return {
            "room_seq": self.room_seq,
            "room_id": self.room_id,
            "status": self.status,
            "total_summary": self.total_summary,
            "reg_id": self.reg_id,
            "reg_dttm": self.reg_dttm.isoformat() if self.reg_dttm else None,
            "upd_id": self.upd_id,
            "upd_dttm": self.upd_dttm.isoformat() if self.upd_dttm else None,
        }