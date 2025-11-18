## 🚀 실시간 스트리밍 (WebSocket) 아키텍처 흐름

`test_real_audio_stream.py` 실행 시, 클라이언트-서버-서비스 간의 상세한 상호작용 흐름입니다.

| 🤖 클라이언트 (`test_real_audio_stream.py`) | 🖥️ API 서버 (FastAPI / `api/stream_endpoints.py`) | 🧠 서비스 (STT/VAD/LLM) |
| :--- | :--- | :--- |
| **(1단계: Job 생성)** | | |
| 1. `requests.post(".../api/v1/stream/create")` (HTTP 요청) | 2. `create_stream_job()` 호출. <br/> `job = StreamingJob()` (`domain`) <br/> `active_jobs[job_id] = job` (`core.config`) <br/> `job_repository.create_job()` (Redis) | 3. `(VADProcessor)`가 `StreamingJob` 내부에 생성됨 |
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
| | 23. `summary_dict` 받음. <br/> `job_repository.update_job(...)` (Redis "completed" 저장). <br/> ➡️ `(WS) "final_summary" 전송 시도`. <br/> `finally:` 블록 진입 (`del active_jobs[job_id]`). | |
| 24. **(메인 스레드)** <br/> `on_close()` 핸들러 실행. <br/> `ws.run_forever()` 종료. <br/> **테스트 스크립트 종료.** | | |