import os
import uuid
from contextlib import asynccontextmanager
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    WebSocket,
    WebSocketDisconnect
)
from typing import Dict

# --- 1. 우리가 만든 서비스 모듈 임포트 ---
from patient_api.services import ollama_service, stt_service, tasks
from patient_api.repositories import job_repository
from patient_api.domain.streaming_job import StreamingJob

# --- 2. 설정 ---
# 업로드된 오디오 파일을 임시 저장할 디렉터리
TEMP_AUDIO_DIR = "temp_audio"

# (F-JOB-02) StreamJobManager: 활성 스트림 작업을 관리하는 전역 딕셔너리
# (사용자님이 제안한 STTHELPER)
active_jobs: Dict[str, StreamingJob] = {}


# --- 3. (기존) Lifespan 이벤트 핸들러 ---
# 서버 시작 시 모델/서비스를 초기화합니다.
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("INFO:     서버가 시작됩니다.")

    # 임시 오디오 디렉터리 생성
    os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
    print(f"INFO:     임시 오디오 디렉터리 확인: {TEMP_AUDIO_DIR}")

    # 1. STT 모델 로드
    stt_service.load_stt_model()

    # 2. Ollama 서버 연결 확인
    await ollama_service.check_ollama_connection()

    yield
    # --- 서버 종료 시 실행될 코드 ---
    print("INFO:     서버가 종료됩니다.")


# --- 4. FastAPI 앱 생성 ---
app = FastAPI(
    title="CureMate STT/Summary API",
    description="음성 대화 STT 및 요약 비동기 API 명세서",
    version="1.0.0",
    lifespan=lifespan  # (3)번의 lifespan 함수를 연결
)


# --- 5. API 엔드포인트 구현 ---

@app.get("/")
def read_root():
    return {"message": "CureMate API (v1) is running!"}


# (F-API-01) 대화 내용 처리 요청 (비동기 작업 생성)
@app.post("/api/v1/conversation/request", status_code=202)
async def create_conversation_request(
        file: UploadFile = File(...)
):
    """
    음성 파일(mp3, wav, m4a 등)을 업로드하여 
    STT 및 요약 작업을 **백그라운드에서 시작**시킵니다.

    즉시 `job_id`를 반환합니다.
    """

    # 1. 고유한 Job ID 생성
    job_id = str(uuid.uuid4())

    # 2. 업로드된 파일을 임시 저장
    try:
        # (보안 참고: 실제 운영 시 파일 확장자/MIME 타입 검증 필수)
        file_ext = file.filename.split(".")[-1]
        temp_file_path = os.path.join(TEMP_AUDIO_DIR, f"{job_id}.{file_ext}")

        # 파일을 비동기로 읽어 디스크에 동기적으로 저장
        # (대용량 파일이면 이 부분도 비동기 I/O(aiofiles) 사용 권장)
        contents = await file.read()
        with open(temp_file_path, "wb") as f:
            f.write(contents)

    except Exception as e:
        print(f"🔴 파일 저장 실패: {e}")
        raise HTTPException(status_code=500, detail=f"파일을 임시 저장하는 데 실패했습니다: {e}")

    # 3. Job을 'pending' 상태로 DB(Redis)에 생성
    # (metadata가 있다면 여기서 함께 전달)
    if not job_repository.create_job(job_id, metadata={"filename": file.filename}):
        raise HTTPException(status_code=500, detail="Job을 생성하는데 실패했습니다 (Redis 연결 확인)")

    # 4. (★핵심) 백그라운드 작업 예약
    # worker.py의 run_stt_and_summary_pipeline 함수를 호출
    tasks.run_stt_and_summary_pipeline.delay(
        job_id,
        temp_file_path
    )

    # 5. (명세서 F-API-01) 즉시 응답 반환
    return {
        "job_id": job_id,
        "status": "pending",
        "message": "작업이 성공적으로 요청되었습니다."
    }


# (F-API-02) 처리 상태 및 결과 조회 (Polling)
@app.get("/api/v1/conversation/result/{job_id}")
def get_conversation_result(job_id: str):
    """
    `job_id`를 사용하여 작업의 현재 상태와 
    중간(STT) 또는 최종(요약) 결과를 조회합니다.
    """

    # 1. DB(Redis)에서 Job 정보 조회
    job = job_repository.get_job(job_id)

    # 2. Job이 없는 경우 404
    if not job:
        raise HTTPException(status_code=404, detail="Job ID를 찾을 수 없습니다.")

    # 3. (명세서 F-API-02) 상태별로 다른 응답 반환
    status = job.get("status")

    if status == "completed":
        # (상태 3) 모든 작업 완료
        return {
            "job_id": job_id,
            "status": "completed",
            "original_transcript": job.get("original_transcript"),
            "structured_summary": job.get("structured_summary"),
            "metadata": job.get("metadata")
        }

    elif status == "transcribed":
        # (상태 2) STT 완료, 요약 진행 중
        return {
            "job_id": job_id,
            "status": "transcribed",
            "original_transcript": job.get("original_transcript")
        }

    elif status == "failed":
        # (상태 4) 작업 실패
        return {
            "job_id": job_id,
            "status": "failed",
            "error_message": job.get("error_message")
        }

    else:  # (status == "pending" or status == "processing")
        # (상태 1) 처리 중
        return {
            "job_id": job_id,
            "status": status
        }


# --- 5.2 (신규) 실시간 스트리밍 API ---

# (F-API-03) 실시간 스트림 작업 생성
@app.post("/api/v1/stream/create", status_code=201)
def create_stream_job():
    """
    (F-API-03) 실시간 화상 통화를 위한 StreamingJob을 생성합니다.
    Redis DB에도 레코드를 생성하고,
    인메모리(active_jobs)에도 Job 인스턴스를 생성합니다.
    """
    # 1. (F-JOB-01) StreamingJob 인스턴스 생성
    job = StreamingJob(metadata={})  # (나중에 metadata=... 전달 가능)

    # 2. (F-JOB-02) 전역 매니저(dict)에 등록
    active_jobs[job.job_id] = job

    # 3. (F-DB-01) Redis에도 'pending' 레코드 생성 (히스토리 저장용)
    if not job_manager.create_job(job.job_id, job.metadata):  #
        # Redis 생성 실패 시, 인메모리 Job도 정리
        del active_jobs[job.job_id]
        raise HTTPException(status_code=500, detail="Job을 Redis에 생성하는데 실패했습니다.")

    print(f"[JobManager] 🟢 새 스트림 작업 생성됨 (Job ID: {job.job_id})")

    # 4. 클라이언트에게 job_id 반환
    return {"job_id": job.job_id}


# (F-API-04) 실시간 STT 스트리밍 (테스트용)
@app.websocket("/ws/v1/stream/{job_id}")
async def conversation_stream(websocket: WebSocket, job_id: str):
    """
    (F-API-04) job_id에 해당하는 스트림 작업을 찾아 WebSocket을 연결합니다.
    (테스트 단계에서는 VAD/STT 대신, 청크 수신 확인만 합니다)
    """

    # 1. (F-JOB-02) 매니저에서 Job 인스턴스 조회
    job = active_jobs.get(job_id)

    if not job:
        print(f"[WebSocket] 🔴 존재하지 않는 Job ID로 연결 시도: {job_id}")
        await websocket.close(code=1008, reason="Job ID not found")
        return

    # 2. 연결 수락
    await websocket.accept()
    print(f"[WebSocket] 🟢 클라이언트 연결됨 (Job: {job_id})")

    # 3. (테스트) 연결 성공 메시지 전송
    await websocket.send_json({
        "type": "connection_success",
        "message": f"Job {job_id}에 성공적으로 연결되었습니다."
    })

    try:
        # --- (테스트) 오디오 청크 수신 루프 ---
        while True:
            # 클라이언트로부터 오디오 바이트 수신
            audio_chunk = await websocket.receive_bytes()

            # (테스트) 실제 VAD 로직 대신, 받았다고 확인만 보냄
            # (나중에 이 부분을 job.process_audio_chunk(audio_chunk)로 교체)
            print(f"[WebSocket] (Job {job_id}) 오디오 청크 수신: {len(audio_chunk)} bytes")

            # (테스트) 클라이언트에게 수신 확인 메시지 전송
            await websocket.send_json({
                "type": "chunk_received",
                "received_bytes": len(audio_chunk)
            })

    except WebSocketDisconnect:
        print(f"[WebSocket] 🟡 클라이언트 연결 끊김 (Job: {job_id})")
        # (나중에 여기에 요약 및 DB 저장 로직 추가)
        # final_transcript = job.get_full_transcript()
        # summary = await ollama_service.get_summary(final_transcript)
        # job_manager.update_job(job.job_id, {"status": "completed", ...})

    except Exception as e:
        print(f"[WebSocket] 🔴 예기치 않은 오류: {e}")

    finally:
        # (F-JOB-02) 매니저(dict)에서 Job 제거 (메모리 누수 방지!)
        if job_id in active_jobs:
            del active_jobs[job_id]
            print(f"[JobManager] 🔴 스트림 작업 제거됨 (메모리 정리): {job_id}")




