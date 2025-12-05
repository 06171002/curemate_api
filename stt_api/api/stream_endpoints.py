"""
WebSocket 스트리밍 엔드포인트 (WebRTC 원본 스트림 대응)

WebRTC 서버가 VAD 요구사항에 맞지 않는 원본 스트림을 전송하는 경우,
서버 측에서 오디오 변환 및 VAD 처리를 수행합니다.

✅ 포맷별 처리 방식:
- Opus, PCM, WebM: 실시간 청크 단위 변환 (권장)
- MP3, AAC: 전체 수신 후 일괄 변환 (비권장, 지연 발생)
"""

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Query
)
from typing import Optional
import wave
import os

from stt_api.core.config import settings
from stt_api.domain.streaming_job import StreamingJob
from stt_api.services.pipeline import StreamPipeline
from stt_api.services.storage import job_manager, JobType, JobStatus
from stt_api.services.audio_converter import AudioStreamConverter
from stt_api.core.config import active_jobs, constants
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import CustomException
from stt_api.services.storage import db_service
from pydantic import BaseModel, Field

logger = get_logger(__name__)

router = APIRouter()


# ✅ 1. 요청 바디를 정의하는 Pydantic 모델 생성
class StreamCreateRequest(BaseModel):
    # 기존 설정 파라미터
    audio_format: str = Field("opus", description="입력 오디오 포맷 (opus, pcm, webm, mp3 등)")
    sample_rate: Optional[int] = Field(None, description="입력 샘플레이트")
    channels: Optional[int] = Field(None, description="입력 채널 수")

    # WebRTC 화상회의 관련
    room_id: Optional[str] = Field(None, description="WebRTC Room ID")
    member_id: Optional[str] = Field(None, description="Member ID")

    # ✅ 도메인 메타데이터 (Body에 포함)
    cure_seq: Optional[int] = Field(None, description="치료 ID")
    cust_seq: Optional[int] = Field(None, description="보호자 ID")
    patient_seq: Optional[int] = Field(None, description="환자 ID")
    mode: Optional[str] = Field(None, description="google 사용 시 Google STT + Gemini")

@router.post("/api/v1/stream/create", status_code=201)
async def create_stream_job(
    request: StreamCreateRequest
):
    """
    실시간 화상 통화를 위한 StreamingJob을 생성합니다.

    Query Parameters:
        - audio_format: WebRTC에서 보내는 오디오 포맷
          * opus, pcm, webm: 실시간 스트리밍 가능 (권장)
          * mp3, aac: 전체 파일 수신 후 처리 (비권장)
        - sample_rate: 입력 오디오 샘플레이트 (선택사항)
        - channels: 입력 오디오 채널 수 (선택사항)
    """
    audio_format = request.audio_format
    room_id = request.room_id
    member_id = request.member_id


    # ✅ 1. 화상 회의 모드인지 확인
    is_conference_mode = bool(room_id and member_id)

    room_seq = None
    if is_conference_mode:
        logger.info(
            "화상 회의 모드 요청",
            room_id=room_id,
            member_id=member_id
        )

        # ✅ 2-1. 방 생성 또는 조회 (JobManager 사용)
        try:
            room_info = await job_manager.get_or_create_room(room_id)
            room_seq = room_info["room_seq"]

            logger.info(
                "방 준비 완료",
                room_id=room_id,
                room_seq=room_seq,
                room_status=room_info.get("status")
            )

        except Exception as e:
            logger.error(
                "방 생성/조회 실패",
                room_id=room_id,
                error=str(e)
            )
            raise HTTPException(
                status_code=500,
                detail=f"방 생성/조회 실패: {str(e)}"
            )

        # ✅ 2-2. 중복 참가자 체크
        existing_job = await job_manager.check_member_exists(room_id, member_id)

        if existing_job:
            # 기존 작업 상태 확인
            existing_status = existing_job.get("status", "").upper()

            # PROCESSING 또는 PENDING 상태면 중복 접속으로 간주
            if existing_status in ["PENDING", "PROCESSING"]:
                logger.warning(
                    "중복 참가자 접속 시도",
                    room_id=room_id,
                    member_id=member_id,
                    existing_job_id=existing_job.get("job_id"),
                    existing_status=existing_status
                )

                raise HTTPException(
                    status_code=409,  # Conflict
                    detail={
                        "error": "DUPLICATE_MEMBER",
                        "message": f"이미 '{member_id}'가 방 '{room_id}'에 참가 중입니다",
                        "existing_job_id": existing_job.get("job_id"),
                        "existing_status": existing_status,
                        "suggestion": "기존 연결을 종료하거나 다른 member_id를 사용하세요"
                    }
                )

            # COMPLETED/FAILED 상태면 재접속 허용
            else:
                logger.info(
                    "참가자 재접속 (이전 작업 완료됨)",
                    room_id=room_id,
                    member_id=member_id,
                    previous_job_id=existing_job.get("job_id"),
                    previous_status=existing_status
                )

    # ✅ 스트리밍 가능한 포맷 확인
    streaming_formats = ["opus", "pcm", "webm", "raw"]
    is_streaming = audio_format.lower() in streaming_formats

    # StreamingJob 생성
    metadata = {
        "input_audio_format": audio_format,
        "input_sample_rate": request.sample_rate,
        "input_channels": request.channels,
        "is_streaming_format": is_streaming,

        # 👇 여기에 도메인 종속 데이터 저장 (JSON 컬럼용)
        "cure_seq": request.cure_seq,
        "cust_seq": request.cust_seq,
        "patient_seq": request.patient_seq,
        "mode": request.mode,
    }

    job = StreamingJob(metadata=metadata)
    active_jobs[job.job_id] = job

    # ==================== 5. DB에 작업 생성 ====================
    try:
        if is_conference_mode:
            # ✅ 화상 회의 모드: room_id, member_id 포함
            success = await job_manager.create_job_with_room(
                job.job_id,
                JobType.REALTIME,
                room_id=room_id,
                member_id=member_id,
                metadata=metadata
            )
        else:
            # 일반 모드: 기존 방식
            success = await job_manager.create_job(
                job.job_id,
                JobType.REALTIME,
                metadata=metadata
            )

        if not success:
            del active_jobs[job.job_id]
            raise HTTPException(status_code=500, detail="작업 생성 실패")

    except HTTPException:
        raise
    except Exception as e:
        if job.job_id in active_jobs:
            del active_jobs[job.job_id]
        logger.error("작업 생성 중 오류", exc_info=True, error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"작업 생성 실패: {str(e)}"
        )

    logger.info(
        "스트림 작업 생성 완료",
        job_id=job.job_id,
        mode="conference" if is_conference_mode else "single",
        room_id=room_id if is_conference_mode else None,
        member_id=member_id if is_conference_mode else None
    )

    warning_message = None
    if not is_streaming:
        warning_message = (
            f"경고: {audio_format}은 실시간 스트리밍에 적합하지 않습니다. "
            "전체 파일 수신 후 처리되므로 지연이 발생할 수 있습니다. "
            "실시간 처리를 원하시면 opus, pcm, webm 포맷을 사용하세요."
        )

    response_data = {
        "job_id": job.job_id,
        "job_type": "REALTIME",
        "status": "pending",
        "audio_config": {
            "target_sample_rate": constants.VAD_SAMPLE_RATE,
            "target_frame_duration_ms": constants.VAD_FRAME_DURATION_MS,
            "input_format": audio_format,
            "is_streaming_format": is_streaming
        }
    }

    # 화상 회의 모드 정보 추가
    if is_conference_mode:
        response_data["conference_info"] = {
            "room_id": room_id,
            "room_seq": room_seq,
            "member_id": member_id,
            "mode": "conference"
        }

    if warning_message:
        response_data["warning"] = warning_message

    return response_data


@router.websocket("/ws/v1/stream/{job_id}")
async def conversation_stream(
    websocket: WebSocket,
    job_id: str
):
    """
    WebRTC 원본 스트림을 받아 VAD 처리 후 STT 수행

    흐름:
    1. 클라이언트가 WebRTC 원본 오디오 전송
    2. AudioStreamConverter로 16kHz/16-bit/Mono/30ms 변환
    3. VADProcessor로 음성 구간 감지
    4. STT 처리 및 실시간 결과 반환
    """

    # 1. Job 조회
    job = active_jobs.get(job_id)

    if not job:
        logger.error("존재하지 않는 Job ID로 연결 시도", job_id=job_id)
        await websocket.close(code=1008, reason="Job ID not found")
        await job_manager.log_error(job_id, "websocket_stream", "존재하지 않는 Job ID")
        return

    # 2. WebSocket 연결 수락
    await websocket.accept()
    logger.info("클라이언트 연결됨 (WebRTC 모드)", job_id=job_id)

    await job_manager.update_status(job_id, JobStatus.PROCESSING)

    await websocket.send_json({
        "type": "connection_success",
        "message": f"Job {job_id}에 성공적으로 연결되었습니다.",
        "vad_config": {
            "sample_rate": constants.VAD_SAMPLE_RATE,
            "frame_duration_ms": constants.VAD_FRAME_DURATION_MS
        }
    })

    # 3. 오디오 변환기 초기화
    audio_format = job.metadata.get("input_audio_format", "opus")
    is_streaming = job.metadata.get("is_streaming_format", True)

    input_sample_rate = job.metadata.get("input_sample_rate") or 48000
    input_channels = job.metadata.get("input_channels") or 2

    # [디버깅용] 파일 저장 준비 (temp_audio 폴더에 저장)
    debug_filename = f"debug_{job_id}.wav"
    debug_file_path = os.path.join(settings.TEMP_AUDIO_DIR, debug_filename)

    # 16k, 1ch, 16bit PCM 포맷으로 WAV 파일 열기
    debug_wav = wave.open(debug_file_path, "wb")
    debug_wav.setnchannels(1)  # 1 채널 (Mono)
    debug_wav.setsampwidth(2)  # 2 Bytes (16-bit)
    debug_wav.setframerate(16000)  # 16000 Hz

    try:
        audio_converter = AudioStreamConverter(
            target_sample_rate=constants.VAD_SAMPLE_RATE,
            target_frame_duration_ms=constants.VAD_FRAME_DURATION_MS,
            input_format=audio_format,
            is_streaming_format=is_streaming,  # ✅ 추가
            input_sample_rate=input_sample_rate,
            input_channels=input_channels
        )

        logger.info(
            "AudioConverter 초기화 완료",
            job_id=job_id,
            input_format=audio_format,
            is_streaming=is_streaming
        )

    except Exception as e:
        error_msg = f"AudioConverter 초기화 실패: {str(e)}"
        logger.error("AudioConverter 초기화 실패", exc_info=True, error=str(e))

        await websocket.send_json({
            "type": "error",
            "message": error_msg
        })
        await websocket.close(code=1011, reason=error_msg)
        return

    # 4. Pipeline 생성 및 시작
    pipeline = StreamPipeline(job, max_workers=3)
    await pipeline.start()

    try:
        # --- 메인 루프: WebRTC 원본 스트림 수신 → 변환 → VAD → STT ---
        chunk_count = 0

        while True:
            # WebRTC에서 원본 오디오 수신
            raw_audio_chunk = await websocket.receive_bytes()
            chunk_count += 1

            # [디버깅 1] 받은 데이터를 그대로 WAV 파일에 기록
            try:
                debug_wav.writeframes(raw_audio_chunk)
            except Exception as e:
                logger.error("디버깅 파일 쓰기 실패", error=str(e))

            # [디버깅 2] 데이터 샘플링 로그 (처음 5개 패킷만 상세 확인)
            if chunk_count <= 5:
                import numpy as np
                # int16으로 해석 시도
                data_np = np.frombuffer(raw_audio_chunk, dtype=np.int16)
                logger.info(
                    "수신 데이터 분석",
                    chunk_num=chunk_count,
                    bytes_len=len(raw_audio_chunk),
                    min_val=data_np.min() if len(data_np) > 0 else 0,
                    max_val=data_np.max() if len(data_np) > 0 else 0,
                    mean_val=data_np.mean() if len(data_np) > 0 else 0,
                    first_10_bytes=list(raw_audio_chunk[:10])  # 헤더 존재 여부 확인용
                )

            # ★ 핵심: 원본 오디오를 VAD 요구사항에 맞게 변환
            try:
                converted_frames = audio_converter.convert_and_buffer(raw_audio_chunk)

                # 변환된 프레임들을 VAD/STT 파이프라인으로 전달
                for frame in converted_frames:
                    async for result in pipeline.process_audio_chunk(frame):
                        try:
                            await websocket.send_json(result)
                        except Exception as send_error:
                            logger.warning(
                                "결과 전송 실패 (클라이언트 연결 끊김)",
                                error=str(send_error)
                            )
                            raise WebSocketDisconnect()

            except Exception as convert_error:
                logger.warning(
                    "오디오 변환 오류",
                    chunk_number=chunk_count,
                    error=str(convert_error)
                )

                # 변환 실패 시에도 계속 진행 (일부 청크 손실 허용)
                continue

    except WebSocketDisconnect:
        logger.info(
            "클라이언트 연결 끊김",
            job_id=job_id,
            chunks_received=chunk_count
        )

        # ✅ 남은 버퍼 처리 (비스트리밍 포맷의 경우 여기서 전체 변환)
        try:
            remaining_audio = audio_converter.flush()
            if remaining_audio:
                logger.info(
                    "남은 오디오 버퍼 처리",
                    bytes=len(remaining_audio),
                    is_streaming=is_streaming
                )

                # ✅ 비스트리밍 포맷: 변환된 전체 PCM을 30ms 청크로 분할
                if not is_streaming:
                    frame_size = constants.VAD_SAMPLE_RATE * (constants.VAD_FRAME_DURATION_MS / 1000.0) * 2
                    frame_size = int(frame_size)

                    total_frames = len(remaining_audio) // frame_size
                    logger.info(
                        "전체 파일 변환 완료, 프레임 분할 시작",
                        total_pcm_bytes=len(remaining_audio),
                        total_frames=total_frames
                    )

                    # ✅ 프레임별로 VAD/STT 처리 (WebSocket 끊김과 관계없이)
                    for i in range(0, len(remaining_audio), frame_size):
                        frame = remaining_audio[i:i+frame_size]
                        if len(frame) < frame_size:
                            break  # 마지막 불완전한 프레임 제외

                        # ✅ 결과를 큐에만 넣고 전송은 시도하지 않음
                        async for result in pipeline.process_audio_chunk(frame):
                            # WebSocket이 끊긴 상태이므로 전송 불가
                            # 결과는 파이프라인 내부에 쌓임
                            pass
                else:
                    # 스트리밍 포맷: 남은 데이터 그대로 처리
                    async for result in pipeline.process_audio_chunk(remaining_audio):
                        pass

        except Exception as e:
            logger.warning("남은 버퍼 처리 실패", error=str(e))

        # ✅ 최종 처리 (STT 완료 대기)
        logger.info("최종 처리 시작 (STT 워커 완료 대기)", job_id=job_id)
        final_result = await pipeline.finalize()

        # 변환 통계 로깅
        converter_stats = audio_converter.get_stats()
        logger.info(
            "오디오 변환 통계",
            job_id=job_id,
            **converter_stats
        )

        # ✅ 결과가 있으면 로깅 (WebSocket 전송은 불가)
        if final_result.get("type") == "final_summary":
            logger.info(
                "최종 요약 완료 (WebSocket 끊김으로 클라이언트 전송 불가)",
                job_id=job_id,
                summary=final_result.get("summary"),
                total_segments=final_result.get("total_segments")
            )

    except Exception as e:
        error_msg = f"예기치 않은 오류: {str(e)}"

        logger.error("WebSocket 처리 오류", exc_info=True, error=str(e))

        await job_manager.log_error(job_id, "websocket", error_msg)
        await job_manager.update_status(job_id, JobStatus.COMPLETED, error_message=error_msg)

        try:
            await websocket.send_json({
                "type": "error",
                "message": error_msg
            })
        except:
            pass

    finally:
        # Job 정리
        if job_id in active_jobs:
            del active_jobs[job_id]
            logger.info("스트림 작업 제거됨 (메모리 정리)", job_id=job_id)


# ==================== 방 정보 조회 엔드포인트 추가 ====================

@router.get("/api/v1/stream/room/{room_id}")
async def get_room_status(room_id: str):
    """
    화상 회의 방 정보 조회

    Returns:
        - 방 상태
        - 참가자 목록
        - 각 참가자별 작업 상태
    """
    room_info = await job_manager.get_room_info(room_id)

    if not room_info:
        raise HTTPException(
            status_code=404,
            detail=f"방 '{room_id}'를 찾을 수 없습니다"
        )

    return room_info


# ==================== 헬스 체크 엔드포인트 ====================

@router.get("/api/v1/stream/health")
async def stream_health_check():
    """
    스트리밍 서비스 헬스 체크
    """
    return {
        "status": "healthy",
        "active_streams": len(active_jobs),
        "vad_config": {
            "sample_rate": constants.VAD_SAMPLE_RATE,
            "frame_duration_ms": constants.VAD_FRAME_DURATION_MS,
            "aggressiveness": constants.VAD_AGGRESSIVENESS
        }
    }


@router.get("/api/v1/stream/stats/{job_id}")
async def get_stream_stats(job_id: str):
    """
    특정 스트림의 실시간 통계 조회
    """
    job = active_jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="활성 스트림을 찾을 수 없습니다")

    return {
        "job_id": job_id,
        "status": job.status,
        "segment_count": len(job.full_transcript),
        "transcript_preview": " ".join(job.full_transcript[-3:])  # 최근 3개 세그먼트
    }