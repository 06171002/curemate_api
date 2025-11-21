"""
Redis 캐시 서비스

job_repository.py에서 이름 변경됨.
Redis를 통한 빠른 데이터 접근 및 Pub/Sub 기능 제공.
"""

import redis
import json
import asyncio
import redis.asyncio as aioredis
import time
from typing import Dict, Any, Optional

from stt_api.core.config import settings  # ✅ 설정 통합
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import (
    StorageException,
    RedisConnectionError,
    JobCreationError,
    JobNotFoundException
)

logger = get_logger(__name__)

# --- Redis 연결 ---

def connect_to_redis(max_retries=5, delay=2):
    """Redis 연결 생성"""
    for i in range(max_retries):
        try:
            client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD,
                decode_responses=True
            )
            client_bytes = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD,
                decode_responses=False
            )

            client.ping()
            logger.info("Redis 연결 성공", attempt=i + 1)
            return client, client_bytes

        except redis.exceptions.ConnectionError as e:
            logger.error("Redis 연결 실패", attempt=i + 1, max_retries=max_retries, error=str(e))
            if i == max_retries - 1:
                raise RedisConnectionError(
                    details=f"최대 재시도 횟수 초과: {str(e)}"
                )
            time.sleep(delay)

# 연결 실패 시 Lazy Loading
def get_redis_client():
    global redis_client
    if redis_client is None:
        redis_client, _ = connect_to_redis()
    return redis_client

redis_client, redis_client_bytes = connect_to_redis()

if not redis_client:
    get_redis_client()

JOB_KEY_PREFIX = "job:med:"


# --- CRUD 함수 ---

def create_job(job_id: str, metadata: Dict[str, Any] = None) -> bool:
    """Redis에 작업 생성"""
    if not redis_client:
        # ✅ CustomException 사용
        raise RedisConnectionError(details="Redis 클라이언트가 초기화되지 않음")

    key = f"{JOB_KEY_PREFIX}{job_id}"
    initial_data = {
        "job_id": job_id,
        "status": "pending",
        "metadata": metadata or {},
        "original_transcript": None,
        "structured_summary": None,
        "error_message": None,
    }

    try:
        redis_client.set(key, json.dumps(initial_data))
        return True
    except Exception as e:
        logger.error("작업 생성 실패", job_id=job_id, error=str(e))
        raise JobCreationError(job_id=job_id, reason=str(e))


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Redis에서 작업 조회"""
    if not redis_client:
        # ✅ CustomException 사용
        raise RedisConnectionError(details="Redis 클라이언트가 초기화되지 않음")

    key = f"{JOB_KEY_PREFIX}{job_id}"

    try:
        data_str = redis_client.get(key)
        if data_str:
            return json.loads(data_str)
        raise JobNotFoundException(job_id=job_id)
    except Exception as e:
        logger.error("작업 조회 실패", job_id=job_id, error=str(e))
        raise StorageException(
            message=f"작업 조회 중 오류 발생",
            details={"job_id": job_id, "error": str(e)}
        )


def update_job(job_id: str, updates: Dict[str, Any]) -> bool:
    """Redis 작업 업데이트"""
    if not redis_client:
        raise RedisConnectionError(details="Redis 클라이언트가 초기화되지 않음")

    key = f"{JOB_KEY_PREFIX}{job_id}"

    try:
        current_data = get_job(job_id)
        if not current_data:
            # ✅ CustomException 사용
            raise JobNotFoundException(job_id=job_id)

        current_data.update(updates)
        redis_client.set(key, json.dumps(current_data))
        return True


    except JobNotFoundException:
        raise
    except Exception as e:
        logger.error("작업 업데이트 실패", job_id=job_id, error=str(e))
        raise StorageException(
            message=f"작업 업데이트 실패",
            details={"job_id": job_id, "error": str(e)}
        )


# --- Pub/Sub 함수 ---

def publish_message(job_id: str, message_data: Dict[str, Any]):
    """Redis 채널에 메시지 발행"""
    if not redis_client:
        raise RedisConnectionError(details="Redis 클라이언트가 초기화되지 않음")

    channel = f"job_events:{job_id}"
    message = json.dumps(message_data)
    redis_client.publish(channel, message)
    logger.info("메시지 발행", channel=channel, message_type=message_data.get('type'))


async def subscribe_to_messages(job_id: str):
    """Redis 채널 구독 (비동기)"""
    async_redis = aioredis.from_url(
        settings.REDIS_URL,  # ✅ config 사용
        encoding="utf-8",
        decode_responses=True
    )

    channel = f"job_events:{job_id}"
    pubsub = async_redis.pubsub()

    try:
        await pubsub.subscribe(channel)
        logger.info("채널 구독 시작", channel=channel)

        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)

            if message and message['type'] == 'message':
                message_data = json.loads(message['data'])
                logger.debug(
                    "메시지 수신",
                    channel=channel,
                    message_type=message_data.get('type')
                )
                yield message_data

            await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        logger.info("구독 취소됨", channel=channel)
    except Exception as e:
        logger.error("구독 중 오류 발생", channel=channel, exc_info=True, error=str(e))
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await async_redis.close()