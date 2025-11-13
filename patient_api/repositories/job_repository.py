import redis
import json
import asyncio
from typing import Dict, Any, Optional

# --- 1. Redis 연결 설정 ---

# 'decode_responses=True'가 중요합니다.
# 이게 없으면 Redis가 문자열 대신 bytes(예: b'hello')를 반환합니다.
try:
    # Docker로 띄운 Redis는 기본적으로 localhost:6379 입니다.
    redis_client = redis.Redis(host='redis', port=6379, decode_responses=True)
    redis_client_bytes = redis.Redis(host='localhost', port=6379, decode_responses=False)
    redis_client.ping()
    print("✅ Redis에 성공적으로 연결되었습니다.")
except redis.exceptions.ConnectionError as e:
    print(f"❌ Redis 연결 실패: {e}")
    print("Docker에서 Redis 컨테이너가 실행 중인지 확인하세요. (docker ps)")
    redis_client = None  # 연결 실패 시 None으로 설정
    redis_client_bytes = None

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
    (SSE 엔드포인트가 사용)
    지정된 job_id 채널을 비동기(async)로 구독(Subscribe)하고 메시지를 반환합니다.
    """
    if not redis_client_bytes:
        raise RuntimeError("Redis(bytes) 연결이 설정되지 않았습니다.")

    channel = f"job_events:{job_id}"
    pubsub = redis_client_bytes.pubsub()
    await pubsub.subscribe(channel)

    print(f"[PubSub] 🎧 (Job {job_id}) 채널 구독 시작...")

    try:
        while True:
            # 비동기로 메시지 대기
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30)

            if message and message['type'] == 'message':
                # 메시지(bytes)를 딕셔너리로 파싱
                message_data = json.loads(message['data'])
                print(f"[PubSub] ⬅️  (Job {job_id}) 메시지 수신: {message_data}")
                yield message_data  # (SSE 핸들러에게 메시지 전달)

            # (만약 30초간 메시지 없으면 timeout -> 루프가 다시 돌며 대기)
            # (실제로는 FastAPI 연결이 끊기면 이 루프도 종료됨)

    except asyncio.CancelledError:
        print(f"[PubSub] 🔌 (Job {job_id}) 구독 취소됨.")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()