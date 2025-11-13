import redis
import json
import asyncio
import redis.asyncio as aioredis
import time
from typing import Dict, Any, Optional


# --- 1. Redis 연결 설정 ---

def connect_to_redis(max_retries=5, delay=2):
    """
    (★신규) Redis가 준비될 때까지 재시도하며 연결합니다.
    """
    for i in range(max_retries):
        try:
            # (★수정) 'host'를 'redis'로 사용
            client = redis.Redis(host='redis', port=6379, decode_responses=True)
            client_bytes = redis.Redis(host='redis', port=6379, decode_responses=False)

            client.ping()  # (★수정) 연결 테스트

            print(f"✅ Redis에 성공적으로 연결되었습니다. (시도 {i + 1}회)")
            return client, client_bytes  # (★수정) 성공 시 클라이언트 반환

        except redis.exceptions.ConnectionError as e:
            print(f"❌ Redis 연결 실패 (시도 {i + 1}/{max_retries}): {e}")
            if i == max_retries - 1:  # 마지막 시도라면 None 반환
                return None, None
            time.sleep(delay)  # 2초 대기 후 재시도


# (★수정) try...except 블록 대신, 새 함수를 호출
redis_client, redis_client_bytes = connect_to_redis()

if not redis_client:
    print("❌ Redis에 최종적으로 연결하지 못했습니다. 서버를 종료합니다.")
    # (실제로는 여기서 예외를 발생시키거나 exit()를 호출하는 것이 좋습니다)

# Redis Key에 사용할 접두사 (Key들이 섞이지 않게 함)
JOB_KEY_PREFIX = "job:med:"


# --- 2. 핵심 함수 구현 ---

def create_job(job_id: str, metadata: Dict[str, Any] = None) -> bool:
    """
    (F-API-01에서 사용)
    새로운 Job을 생성하고 'pending' 상태로 Redis에 저장합니다.
    """
    if not redis_client:
        return False

    key = f"{JOB_KEY_PREFIX}{job_id}"

    # DB에 저장할 초기 데이터 구조
    initial_data = {
        "job_id": job_id,
        "status": "pending",
        "metadata": metadata or {},
        "original_transcript": None,  # STT 결과가 저장될 곳
        "structured_summary": None,  # 요약 결과가 저장될 곳
        "error_message": None,  # 실패 시 에러 메시지
        # "created_at": ... (필요시 타임스탬프 추가)
    }

    try:
        # JSON 문자열로 변환하여 Redis에 SET
        redis_client.set(key, json.dumps(initial_data))
        return True
    except Exception as e:
        print(f"[JobManager] 작업 생성 실패 (Job {job_id}): {e}")
        return False


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """
    (F-API-02에서 사용)
    Job ID로 Redis에서 작업 데이터를 조회합니다.
    """
    if not redis_client:
        return None

    key = f"{JOB_KEY_PREFIX}{job_id}"

    try:
        # Redis에서 JSON 문자열을 가져옴
        data_str = redis_client.get(key)

        if data_str:
            # JSON 문자열을 Python 딕셔너리로 파싱하여 반환
            return json.loads(data_str)
        else:
            # 존재하지 않는 Job ID
            return None
    except Exception as e:
        print(f"[JobManager] 작업 조회 실패 (Job {job_id}): {e}")
        return None


def update_job(job_id: str, updates: Dict[str, Any]) -> bool:
    """
    (백그라운드 워커에서 사용)
    기존 Job 데이터에 새로운 정보(updates 딕셔너리)를 덮어씁니다.
    이 함수 하나로 상태 변경, STT 결과 저장, 요약 저장을 모두 처리합니다.
    """
    if not redis_client:
        return False

    key = f"{JOB_KEY_PREFIX}{job_id}"

    try:
        # 1. (Get) 현재 데이터를 먼저 읽어옵니다. (Read)
        current_data = get_job(job_id)
        if not current_data:
            print(f"[JobManager] 업데이트할 작업을 찾을 수 없음 (Job {job_id})")
            return False

        # 2. (Modify) 읽어온 딕셔너리에 'updates' 딕셔너리의 내용을 덮어씁니다.
        current_data.update(updates)

        # 3. (Set) 변경된 전체 딕셔너리를 다시 JSON 문자열로 저장합니다. (Write)
        redis_client.set(key, json.dumps(current_data))
        return True

    except Exception as e:
        print(f"[JobManager] 작업 업데이트 실패 (Job {job_id}): {e}")
        return False


# --- 3. (신규) Pub/Sub 함수 ---

def publish_message(job_id: str, message_data: Dict[str, Any]):
    """
    (Celery 워커가 사용)
    지정된 job_id 채널로 메시지를 발행(Publish)합니다.
    """
    if not redis_client:
        return

    channel = f"job_events:{job_id}"
    message = json.dumps(message_data)
    redis_client.publish(channel, message)
    print(f"[PubSub] ➡️  (Job {job_id}) 채널로 메시지 발행: {message[:50]}...")


async def subscribe_to_messages(job_id: str):
    """
    (★수정) 비동기 Redis 클라이언트를 사용하여 메시지를 구독합니다.
    """
    # ★ 비동기 Redis 클라이언트 생성
    async_redis = aioredis.from_url(
        "redis://redis:6379",
        encoding="utf-8",
        decode_responses=True
    )

    channel = f"job_events:{job_id}"
    pubsub = async_redis.pubsub()

    try:
        await pubsub.subscribe(channel)
        print(f"[PubSub] 🎧 (Job {job_id}) 채널 구독 시작...")

        while True:
            # ★ 비동기로 메시지 대기
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)

            if message and message['type'] == 'message':
                # 메시지를 딕셔너리로 파싱
                message_data = json.loads(message['data'])
                print(f"[PubSub] ⬅️  (Job {job_id}) 메시지 수신: {message_data}")
                yield message_data

            # 짧은 대기 (CPU 사용량 감소)
            await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        print(f"[PubSub] 🔌 (Job {job_id}) 구독 취소됨.")
    except Exception as e:
        print(f"[PubSub] 🔴 구독 중 오류: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await async_redis.close()