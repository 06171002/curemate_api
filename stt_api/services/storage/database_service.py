from typing import Dict, Any, Optional, List
from datetime import datetime
import json
import sys
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


class DatabaseService:
    """
    데이터베이스 추상화 레이어
    현재는 Redis를 사용하지만, 나중에 PostgreSQL/MySQL 등으로 쉽게 교체 가능
    """

    def __init__(self):
        self.db_type = "redis"  # 나중에 "postgresql", "mysql" 등으로 변경 가능
        logger.info("DB 서비스 초기화", db_type=self.db_type)

    # ==================== T_STT_JOB 관련 ====================

    def create_stt_job(self, job_id: str, job_type: str, metadata: Dict = None) -> bool:
        """
        T_STT_JOB 테이블에 새로운 작업 생성

        Args:
            job_id: 작업 고유 ID (UUID)
            job_type: 'BATCH' 또는 'REALTIME'
            metadata: 추가 메타데이터
        """
        try:
            job_data = {
                "job_id": job_id,
                "job_type": job_type,
                "status": "PENDING",
                "original_transcript": None,
                "structured_summary": None,
                "error_message": None,
                "reg_dttm": datetime.now().isoformat(),
                "upd_dttm": datetime.now().isoformat(),
                "metadata": metadata or {}
            }

            logger.debug(
                "T_STT_JOB 레코드 생성",
                job_id=job_id,
                job_type=job_type
            )
            # TODO: 실제 DB INSERT 구문으로 교체
            # INSERT INTO T_STT_JOB (job_id, job_type, status, reg_dttm, upd_dttm)
            # VALUES (%s, %s, %s, %s, %s)

            return True

        except Exception as e:
            logger.error("T_STT_JOB 생성 실패", exc_info=True, job_id=job_id, error=str(e))
            return False

    def get_stt_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """T_STT_JOB 테이블에서 작업 조회"""
        try:
            logger.debug("T_STT_JOB 조회", job_id=job_id)
            # TODO: 실제 DB SELECT 구문으로 교체
            # SELECT * FROM T_STT_JOB WHERE job_id = %s

            return None

        except Exception as e:
            logger.error("T_STT_JOB 조회 실패", exc_info=True, job_id=job_id, error=str(e))
            return None

    def update_stt_job_status(self, job_id: str, status: str,
                              transcript: str = None,
                              summary: Dict = None,
                              error_message: str = None) -> bool:
        """
        T_STT_JOB 테이블 상태 업데이트

        Args:
            status: PENDING, PROCESSING, TRANSCRIBED, COMPLETED, FAILED
        """
        try:
            updates = {
                "status": status,
                "upd_dttm": datetime.now().isoformat()
            }

            if transcript:
                updates["original_transcript"] = transcript
            if summary:
                updates["structured_summary"] = json.dumps(summary)
            if error_message:
                updates["error_message"] = error_message

            logger.debug(
                "T_STT_JOB 업데이트",
                job_id=job_id,
                status=status
            )
            # TODO: 실제 DB UPDATE 구문으로 교체
            # UPDATE T_STT_JOB
            # SET status=%s, original_transcript=%s, structured_summary=%s, upd_dttm=%s
            # WHERE job_id=%s

            return True

        except Exception as e:
            logger.error(
                "T_STT_JOB 업데이트 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            return False

    # ==================== T_STT_SEGMENT 관련 ====================

    def insert_stt_segment(self, job_id: str, segment_text: str,
                           start_time: float = None, end_time: float = None) -> bool:
        """
        T_STT_SEGMENT 테이블에 STT 세그먼트 삽입
        (실시간 스트리밍 작업에서 사용)
        """
        try:
            segment_data = {
                "job_id": job_id,
                "segment_text": segment_text,
                "start_time": start_time,
                "end_time": end_time,
                "reg_dttm": datetime.now().isoformat(),
                "upd_dttm": datetime.now().isoformat()
            }

            logger.debug(
                "T_STT_SEGMENT 삽입",
                job_id=job_id,
                text_preview=segment_text[:50]
            )
            # TODO: 실제 DB INSERT 구문으로 교체
            # INSERT INTO T_STT_SEGMENT (job_id, segment_text, start_time, end_time, reg_dttm)
            # VALUES (%s, %s, %s, %s, %s)

            return True

        except Exception as e:
            logger.error(
                "T_STT_SEGMENT 삽입 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            return False

    def get_stt_segments(self, job_id: str) -> List[Dict[str, Any]]:
        """특정 job의 모든 세그먼트 조회"""
        try:
            logger.debug("T_STT_SEGMENT 조회", job_id=job_id)
            # TODO: 실제 DB SELECT 구문으로 교체
            # SELECT * FROM T_STT_SEGMENT WHERE job_id = %s ORDER BY start_time

            return []

        except Exception as e:
            logger.error(
                "T_STT_SEGMENT 조회 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            return []

    # ==================== T_STT_ERROR_LOG 관련 ====================

    def log_error(self, job_id: str, service_name: str, error_message: str) -> bool:
        """
        T_STT_ERROR_LOG 테이블에 에러 로그 기록

        Args:
            service_name: 'celery_task', 'websocket_stream', 'stt_service', 'ollama_service' 등
        """
        try:
            log_data = {
                "job_id": job_id,
                "service_name": service_name,
                "error_message": error_message,
                "reg_dttm": datetime.now().isoformat()
            }

            logger.error(
                "T_STT_ERROR_LOG 기록",
                job_id=job_id,
                service_name=service_name,
                error_preview=error_message[:100]
            )

            # TODO: 실제 DB INSERT 구문으로 교체
            # INSERT INTO T_STT_ERROR_LOG (job_id, service_name, error_message, reg_dttm)
            # VALUES (%s, %s, %s, %s)

            return True

        except Exception as e:
            logger.error(
                "T_STT_ERROR_LOG 기록 실패",
                exc_info=True,
                job_id=job_id,
                error=str(e)
            )
            return False

    def get_error_logs(self, job_id: str) -> List[Dict[str, Any]]:
        """특정 job의 모든 에러 로그 조회"""
        try:
            logger.debug("T_STT_ERROR_LOG 조회", job_id=job_id)
            # TODO: 실제 DB SELECT 구문으로 교체
            # SELECT * FROM T_STT_ERROR_LOG WHERE job_id = %s ORDER BY reg_dttm DESC

            return []

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