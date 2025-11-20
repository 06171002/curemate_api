# STT/Summary API

실시간 STT(Speech-to-Text) 및 요약 API 서버입니다.

이 프로젝트는 `FastAPI`, `Celery`, `Redis`, `faster-whisper`를 Docker Compose로 실행하고, `Ollama`는 로컬 호스트(Host) PC에서 실행합니다.

---

## 🚀 실행 방법

### 1. 사전 준비 (총 3가지)

1.  **Docker Desktop**을 설치하고 실행해야 합니다.
2.  **Ollama**를 **호스트 PC(Windows/Mac)에 직접 설치**해야 합니다.
3.  Ollama에서 사용할 모델(`gemma3`)을 미리 받아야 합니다.
    ```bash
    ollama pull gemma3
    ```

### 2. 프로젝트 클론

### 3. 실행

1.  **[터미널 1]** 로컬 PC(Windows)에서 `Ollama` 서버를 **0.0.0.0 호스트**로 실행 (`$env:OLLAMA_HOST="0.0.0.0"`, `ollama serve`)하고 방화벽을 허용합니다.
2.  **[터미널 2]** `docker-compose up -d --build`를 실행합니다.

----------------------------

## 📂 프로젝트 구조

<details>
<summary>👉 디렉토리 구조 펼치기/접기</summary>
```bash
stt_api/
├── __init__.py                    # 패키지 초기화
├── main.py                        # FastAPI 애플리케이션 진입점
│
├── core/                          # 🔧 핵심 설정 및 인프라
│   ├── config.py                  # 환경 설정 (Settings, Constants)
│   ├── celery_config.py           # Celery 작업 큐 설정
│   ├── logging_config.py          # 구조화된 로깅 (JSON/Color)
│   └── exceptions.py              # 커스텀 예외 정의
│
├── domain/                        # 📦 도메인 모델
│   └── streaming_job.py           # 실시간 스트림 작업 모델 (VAD 등)
│
├── services/                      # 🛠️ 비즈니스 로직 서비스
│   ├── stt/                       # 🎤 음성-텍스트 변환
│   │   ├── whisper_service.py     # Whisper 모델 핸들러
│   │   └── vad_processor.py       # 음성 활동 감지 (VAD)
│   │
│   ├── llm/                       # 🤖 LLM 요약 서비스
│   │   ├── ollama_service.py      # Ollama (Local) 구현체
│   │   └── lm_service.py          # LM Studio 구현체
│   │
│   ├── storage/                   # 💾 데이터 저장소
│   │   ├── job_manager.py         # 작업 생명주기 관리 (DB+Redis)
│   │   └── cache_service.py       # Redis 캐시 & Pub/Sub
│   │
│   ├── pipeline/                  # 🔄 워크플로우 파이프라인
│   │   ├── batch_pipeline.py      # 배치 처리 (파일 업로드)
│   │   └── stream_pipeline.py     # 스트리밍 처리 (WebSocket)
│   │
│   └── tasks.py                   # ⚙️ Celery 백그라운드 작업
│
└── api/                           # 🌐 API 엔드포인트
    ├── batch_endpoints.py         # 배치 작업 API
    └── stream_endpoints.py        # 실시간 스트림 API
</details>

---------------------
## 💾 파일 후처리 (SSE) 아키텍처 흐름

`POST /api/v1/conversation/request` (파일 업로드)와 `GET /api/v1/conversation/stream-events/{job_id}` (SSE 스트림) 요청 시의 상세 흐름입니다.

sequenceDiagram
    participant C as Client (App)
    participant API as API Server
    participant R as Redis (Pub/Sub)
    participant W as Celery Worker
    participant L as LLM (Ollama)

    Note over C, API: 1단계: 작업 요청
    C->>API: POST /request (Audio File)
    API->>R: Create Job (Pending)
    API->>W: Task Queueing (Celery)
    API-->>C: Return {job_id}

    Note over C, API: 2단계: 실시간 이벤트 구독
    C->>API: GET /stream-events/{job_id}
    API->>R: Subscribe (Pub/Sub)

    Note over W, L: 3단계: 백그라운드 처리
    W->>W: STT Processing (Whisper)
    loop Every Segment
        W->>R: Publish "transcript_segment"
        R-->>API: Event Message
        API-->>C: SSE Send (Segment Text)
    end

    W->>L: Request Summary (Full Text)
    L-->>W: Return JSON Summary
    W->>R: Publish "final_summary"
    R-->>API: Event Message
    API-->>C: SSE Send (Final Summary)




---------------------


## 🚀 실시간 스트리밍 (WebSocket) 아키텍처 흐름

`test_real_audio_stream.py` 실행 시, 클라이언트-서버-서비스 간의 상세한 상호작용 흐름입니다.

| 🤖 클라이언트 (`test_real_audio_stream.py`) | 🖥️ API 서버 (FastAPI / `api/stream_endpoints.py`) | 🧠 서비스 (STT/VAD/LLM) |
| :--- | :--- | :--- |
| **(1단계: Job 생성)** | | |
| 1. `requests.post(".../api/v1/stream/create")` (HTTP 요청) | 2. `create_stream_job()` 호출. <br/> `job = StreamingJob()` (`domain`) <br/> `active_jobs[job_id] = job` (`core.config`) <br/> `cache_service.create_job()` (Redis) | 3. `(VADProcessor)`가 `StreamingJob` 내부에 생성됨 |
| 4. `{"job_id": ...}` 응답 수신 | 5. `job_id` 반환 | |
| **(2단계: WebSocket 연결)** | | |
| 6. `ws.run_forever()`로 `ws://.../{job_id}` 연결 (메인 스레드 대기) | 7. `conversation_stream()` 핸들러 시작. <br/> `job = active_jobs.get(job_id)` <br/> `await websocket.accept()` (연결 수락). <br/> ➡️ `(WS) "connection_success" 전송` | |
| 8. `on_open()` 핸들러 실행: <br/> `send_audio_stream()` 함수를 **새 스레드**로 시작. | | |
| 9. `on_message()` 핸들러 실행: <br/> "connection_success" 메시지 수신 및 출력. | | |
| **(3단계: STT/VAD 처리 루프)** | | |
| 10. **(오디오 스레드)** <br/> `AudioSegment.from_file(MP3)` <br/> `audio.set_frame_rate(16000)...` (PCM 변환) <br/> `for chunk in ...:` 루프 시작 <br/> ➡️ `(WS) 960 바이트 청크 전송` <br/> `time.sleep(0.030)` | 11. **(서버 비동기 루프)** <br/> `await websocket.receive_bytes()` (청크 수신) <br/> `job.process_audio_chunk(chunk)` 호출 | 12. **(`utils/vad.py`)** <br/> `VADProcessor.process_chunk()`가 `speech_buffer`에 청크 저장 |
| ... (청크 계속 전송) | ... (청크 수신 ➡️ VAD 전달 반복) | ... (음성 청크 `buffer`에 누적) |
| | 13. **침묵 감지!** (`max_silence_frames` 도달) | 14. (`utils/vad.py`) `segment_bytes` (오디오 덩어리) 반환 |
| | 15. `if segment_bytes:` True! <br/> `await asyncio.to_thread(stt_service...)` (STT를 **별도 스레드**에서 실행) | 16. **(`services/stt_service.py`)** <br/> `transcribe_segment_from_bytes()` (동기) 실행 <br/> (STT 처리...) <br/> `segment_text` 반환 |
| | 17. `segment_text` 수신 (`job` 객체에 저장) <br/> ➡️ `(WS) "transcript_segment" 전송` | |
| 18. `on_message()` 핸들러 실행: <br/> `{"type":"transcript_segment", ...}` 수신 및 출력 | (루프가 11번으로 돌아가 다음 청크 대기) | (다음 세그먼트 대기) |
| **(4단계: 연결 종료)** | | |
| 19. **(오디오 스레드)** <br/> `for` 루프 종료. <br/> `time.sleep(10)` (10초 대기) <br/> ➡️ `ws.close()` (연결 종료 요청) | 20. **(서버 비동기 루프)** <br/> `await websocket.receive_bytes()`에서 `WebSocketDisconnect` 예외 발생. <br/> `except WebSocketDisconnect:` 블록 진입. | |
| | 21. `final_transcript = job.get_full_transcript()` (누적된 텍스트 가져오기) <br/> `await ollama_service.get_summary(...)` 호출 | 22. **(`services/ollama_service.py`)** <br/> `get_summary()` 실행 (요약 요청) |
| | 23. `summary_dict` 받음. <br/> `cache_service.update_job(...)` (Redis "completed" 저장). <br/> ➡️ `(WS) "final_summary" 전송 시도`. <br/> `finally:` 블록 진입 (`del active_jobs[job_id]`). | |
| 24. **(메인 스레드)** <br/> `on_close()` 핸들러 실행. <br/> `ws.run_forever()` 종료. <br/> **테스트 스크립트 종료.** | | |