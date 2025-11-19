# patient_api/services/storage/cache_service.py

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

from patient_api.core.config import settings  # ✅ 설정 통합


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
            print(f"✅ Redis 연결 성공 (시도 {i + 1}회)")
            return client, client_bytes

        except redis.exceptions.ConnectionError as e:
            print(f"❌ Redis 연결 실패 (시도 {i + 1}/{max_retries}): {e}")
            if i == max_retries - 1:
                return None, None
            time.sleep(delay)


redis_client, redis_client_bytes = connect_to_redis()

if not redis_client:
    print("❌ Redis 최종 연결 실패")

JOB_KEY_PREFIX = "job:med:"


# --- CRUD 함수 ---

def create_job(job_id: str, metadata: Dict[str, Any] = None) -> bool:
    """Redis에 작업 생성"""
    if not redis_client:
        return False

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
        print(f"[CacheService] 작업 생성 실패 (Job {job_id}): {e}")
        return False


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Redis에서 작업 조회"""
    if not redis_client:
        return None

    key = f"{JOB_KEY_PREFIX}{job_id}"

    try:
        data_str = redis_client.get(key)
        if data_str:
            return json.loads(data_str)
        return None
    except Exception as e:
        print(f"[CacheService] 작업 조회 실패 (Job {job_id}): {e}")
        return None


def update_job(job_id: str, updates: Dict[str, Any]) -> bool:
    """Redis 작업 업데이트"""
    if not redis_client:
        return False

    key = f"{JOB_KEY_PREFIX}{job_id}"

    try:
        current_data = get_job(job_id)
        if not current_data:
            print(f"[CacheService] 업데이트할 작업 없음 (Job {job_id})")
            return False

        current_data.update(updates)
        redis_client.set(key, json.dumps(current_data))
        return True

    except Exception as e:
        print(f"[CacheService] 작업 업데이트 실패 (Job {job_id}): {e}")
        return False


# --- Pub/Sub 함수 ---

def publish_message(job_id: str, message_data: Dict[str, Any]):
    """Redis 채널에 메시지 발행"""
    if not redis_client:
        return

    channel = f"job_events:{job_id}"
    message = json.dumps(message_data)
    redis_client.publish(channel, message)
    print(f"[CacheService] ➡️  메시지 발행: {channel}")


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
        print(f"[CacheService] 🎧 채널 구독 시작: {channel}", flush=True)

        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)

            if message and message['type'] == 'message':
                message_data = json.loads(message['data'])
                print(f"[CacheService] ⬅️  메시지 수신: {message_data.get('type')}", flush=True)
                yield message_data

            await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        print(f"[CacheService] 🔌 구독 취소: {channel}", flush=True)
    except Exception as e:
        print(f"[CacheService] 🔴 구독 오류: {e}", flush=True)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await async_redis.close()