"""
데이터베이스 연결 및 세션 관리

SQLAlchemy + aiomysql을 사용한 비동기 DB 연결
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker
)
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import QueuePool
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import text

from stt_api.core.config import settings
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)

# ==================== SQLAlchemy Base ====================
Base = declarative_base()

# ==================== 비동기 엔진 생성 ====================
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,  # SQL 쿼리 로깅
    # ✅ [변경] Celery 등 멀티 프로세스/루프 환경에서는 풀링을 비활성화(NullPool)해야 안전합니다.
    poolclass=NullPool,
)

# ==================== 세션 팩토리 ====================
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # commit 후에도 객체 접근 가능
    autoflush=False,  # 명시적 flush 필요
    autocommit=False,  # 명시적 commit 필요
)


# ==================== 세션 의존성 (FastAPI용) ====================
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI Dependency로 사용할 DB 세션 생성기

    사용 예시:
        @app.get("/jobs/{job_id}")
        async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(STTJob).where(STTJob.job_id == job_id))
            return result.scalar_one_or_none()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("DB 세션 오류", exc_info=True, error=str(e))
            raise
        finally:
            await session.close()


# ==================== 데이터베이스 초기화 ====================
async def init_database():
    """
    데이터베이스 연결 테스트 및 초기화

    서버 시작 시 호출됩니다.
    """
    try:
        logger.info(
            "데이터베이스 연결 시도",
            db_type=settings.DB_TYPE,
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=settings.DB_NAME
        )

        async with engine.begin() as conn:
            # 연결 테스트
            await conn.execute(text("SELECT 1"))

        logger.info(
            "데이터베이스 연결 성공",
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW
        )

    except Exception as e:
        logger.critical(
            "데이터베이스 연결 실패",
            exc_info=True,
            error=str(e),
            host=settings.DB_HOST,
            database=settings.DB_NAME
        )
        raise


async def close_database():
    """
    데이터베이스 연결 종료

    서버 종료 시 호출됩니다.
    """
    try:
        await engine.dispose()
        logger.info("데이터베이스 연결 종료됨")
    except Exception as e:
        logger.error("데이터베이스 종료 중 오류", exc_info=True, error=str(e))


# ==================== 트랜잭션 헬퍼 ====================
@asynccontextmanager
async def get_transaction() -> AsyncGenerator[AsyncSession, None]:
    """
    독립적인 트랜잭션 컨텍스트 매니저

    사용 예시:
        async with get_transaction() as session:
            job = STTJob(job_id="123", status="PENDING")
            session.add(job)
            # 자동으로 commit됨
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error("트랜잭션 롤백", exc_info=True, error=str(e))
            raise
        finally:
            await session.close()


# ==================== 헬스 체크 ====================
async def check_database_health() -> bool:
    """
    데이터베이스 연결 상태 확인

    헬스 체크 엔드포인트에서 사용됩니다.

    Returns:
        연결 정상 여부
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("DB 헬스 체크 실패", error=str(e))
        return False