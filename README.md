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

📁 프로젝트 구조

stt_api/
├── __init__.py                    # 패키지 초기화
├── main.py                        # FastAPI 애플리케이션 진입점 (서버 실행, 라우터 등록)
│
├── core/                          # 🔧 핵심 설정 및 인프라
│   ├── __init__.py
│   ├── config.py                  # 환경 설정 관리 (Settings, Constants, active_jobs)
│   ├── celery_config.py           # Celery 작업 큐 설정 (브로커, 워커)
│   ├── logging_config.py          # 구조화된 로깅 시스템 (JSON/컬러 포맷, StructuredLogger)
│   └── exceptions.py              # 커스텀 예외 정의 (CustomException, 도메인별 예외)
│
├── domain/                        # 📦 도메인 모델 (비즈니스 엔티티)
│   ├── __init__.py
│   └── streaming_job.py           # 실시간 스트림 작업 모델 (VAD, 대화록 관리)
│
├── services/                      # 🛠️ 비즈니스 로직 서비스
│   ├── __init__.py
│   │
│   ├── stt/                       # 🎤 음성-텍스트 변환 (STT)
│   │   ├── __init__.py            # 서비스 진입점 (함수 노출)
│   │   ├── whisper_service.py     # Whisper 모델 STT (배치/스트리밍 변환)
│   │   └── vad_processor.py       # 음성 활동 감지 (VAD, 실시간 세그먼트 분리)
│   │
│   ├── llm/                       # 🤖 LLM 요약 서비스
│   │   ├── __init__.py            # 프로바이더 자동 선택 (Ollama/LM Studio)
│   │   ├── base_llm_service.py    # LLM 서비스 추상 클래스 (인터페이스 정의)
│   │   ├── ollama_service.py      # Ollama LLM 구현체 (로컬 LLM)
│   │   └── lm_service.py          # LM Studio 구현체 (OpenAI 호환 API)
│   │
│   ├── storage/                   # 💾 데이터 저장 및 작업 관리
│   │   ├── __init__.py            # 서비스 통합 진입점
│   │   ├── job_manager.py         # 작업 생명주기 통합 관리 (DB + Redis 조율)
│   │   ├── database_service.py    # DB 추상화 레이어 (PostgreSQL/MySQL 대비)
│   │   └── cache_service.py       # Redis 캐시 서비스 (빠른 조회, Pub/Sub)
│   │
│   ├── pipeline/                  # 🔄 워크플로우 파이프라인
│   │   ├── __init__.py
│   │   ├── batch_pipeline.py      # 배치 처리 파이프라인 (파일 업로드 → STT → 요약)
│   │   └── stream_pipeline.py     # 실시간 스트리밍 파이프라인 (WebSocket → VAD → STT → 요약)
│   │
│   └── tasks.py                   # ⚙️ Celery 백그라운드 작업 (비동기 파이프라인 실행)
│
└── api/                           # 🌐 FastAPI 엔드포인트
    ├── __init__.py
    ├── batch_endpoints.py         # 배치 작업 API (POST /request, GET /result, SSE /stream-events)
    └── stream_endpoints.py        # 실시간 스트림 API (POST /create, WebSocket /ws)



---------------------
## 💾 파일 후처리 (SSE) 아키텍처 흐름

`POST /api/v1/conversation/request` (파일 업로드)와 `GET /api/v1/conversation/stream-events/{job_id}` (SSE 스트림) 요청 시의 상세 흐름입니다.

| 💻 클라이언트 (App) | 🖥️ API 서버 (FastAPI / `api` 컨테이너) | 🏭 워커 (Celery / `worker` 컨테이너) |
| :--- | :--- | :--- |
| **(1단계: 작업 요청)** | | |
| 1. `POST /.../request` (파일 첨부) ➡️ | 2. `create_conversation_request()` 호출. <br/> `job_id` 생성 및 파일 저장 (`temp_audio/`) <br/> `cache_service.create_job()` (Redis "pending" 저장) <br/> `tasks.run_stt_and_summary_pipeline.delay(...)` (Celery에 작업 등록) | |
| 3. `{"job_id": ...}` 응답 수신. ➡️ <br/> 4. `GET /.../stream-events/{job_id}` (SSE) 연결 | 5. `stream_events()` 핸들러 시작. <br/> `event_generator()` 시작. <br/> `cache_service.subscribe_to_messages()` (Redis Pub/Sub 구독) | |
| **(2단계: STT/요약 (백그라운드))** | | |
| | | 6. **`run_stt_and_summary_pipeline()`** 실행. <br/> 7. `asyncio.run(_run_pipeline_async(...))` 호출. |
| | | 8. `_run_pipeline_async()` 시작. <br/> `cache_service.update_job("processing")` (Redis DB 업데이트) <br/> 9. `for segment in stt_service.transcribe_audio_streaming(...)` 루프 시작. |
| | | 10. (`stt_service.py`) `transcribe_audio_streaming()`가 `_model.transcribe()` (VAD 포함)를 호출. <br/> 11. 세그먼트가 감지되면 `yield segment_text`. |
| 12. `transcript_segment` 이벤트 수신 (STT 결과) ➡️ | 13. (`event_generator`) `subscribe_to_messages`가 Pub/Sub 메시지 수신. <br/> `yield {"event": "transcript_segment", ...}` | 14. (`_run_pipeline_async`) <br/> `cache_service.publish_message(...)` (Redis Pub/Sub 발행) |
| ... (STT `for` 루프 반복) ... | ... (SSE 메시지 수신 및 `yield` 반복) ... | ... (STT 세그먼트 `yield` 및 Pub/Sub 발행 반복) ... |
| | | 15. (`_run_pipeline_async`) `for` 루프 종료. <br/> `full_transcript` 조립. <br/> `cache_service.update_job("transcribed", ...)` (Redis DB 업데이트) <br/> 16. `await ollama_service.get_summary(...)` 호출 |
| | | 17. (`ollama_service.py`) (또는 `llm_service.py`) <br/> `get_summary()`가 `ollama`에 요약 요청 (HTTP) |
| | | 18. (`_run_pipeline_async`) `summary_dict` 받음. <br/> `cache_service.publish_message(...)` (Redis Pub/Sub 발행) <br/> `cache_service.update_job("completed", ...)`. |
| 19. `final_summary` 이벤트 수신 ➡️ | 20. (`event_generator`) `subscribe_to_messages`가 Pub/Sub 메시지 수신. <br/> `yield {"event": "final_summary", ...}`. <br/> `break;` (SSE 연결 종료) | |




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