import uuid
from typing import List, Dict, Optional
from stt_api.core.config import settings, constants
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)


class StreamingJob:
    """
    (F-JOB-01) 각 실시간 스트림의 고유 상태를 관리하는 클래스

    ✅ faster-whisper와 WhisperLiveKit 모두 지원
    """

    def __init__(self, metadata: Dict = None):
        self.job_id: str = str(uuid.uuid4())
        self.metadata: Dict = metadata or {}

        # ✅ STT 엔진에 따라 다른 초기화
        self.use_vad = settings.STT_ENGINE == "faster-whisper"

        if self.use_vad:
            # --- faster-whisper 모드: VAD 사용 ---
            from stt_api.services.stt.vad_processor import VADProcessor

            self.vad_processor = VADProcessor(
                sample_rate=constants.VAD_SAMPLE_RATE,
                frame_duration_ms=constants.VAD_FRAME_DURATION_MS,
                vad_aggressiveness=constants.VAD_AGGRESSIVENESS,
                min_speech_frames=constants.VAD_MIN_SPEECH_FRAMES,
                max_silence_frames=constants.VAD_MAX_SILENCE_FRAMES
            )
            self.audio_buffer = None

            logger.info(
                "StreamingJob 생성 (faster-whisper 모드)",
                job_id=self.job_id,
                vad_sample_rate=constants.VAD_SAMPLE_RATE,
                vad_frame_duration=constants.VAD_FRAME_DURATION_MS
            )
        else:
            # --- WhisperLiveKit 모드: VAD 불필요, 버퍼만 사용 ---
            self.vad_processor = None
            self.audio_buffer = bytearray()  # 청크를 누적할 버퍼
            self.buffer_threshold = 16000 * 2 * 1  # 1초치 오디오 (16kHz, 16-bit)

            logger.info(
                "StreamingJob 생성 (WhisperLiveKit 모드)",
                job_id=self.job_id,
                buffer_threshold_bytes=self.buffer_threshold
            )

        # 공통 속성
        self.full_transcript: List[str] = []
        self.current_prompt_context: str = ""
        self.status: str = "processing"

    def process_audio_chunk(self, audio_chunk: bytes) -> Optional[bytes]:
        """
        오디오 청크 처리

        Args:
            audio_chunk: 오디오 바이트 청크

        Returns:
            - faster-whisper: VAD가 감지한 세그먼트 (없으면 None)
            - WhisperLiveKit: 버퍼가 임계값을 넘으면 버퍼 내용 반환
        """
        if self.use_vad:
            # --- faster-whisper 모드: VAD 처리 ---
            segment_bytes = self.vad_processor.process_chunk(audio_chunk)
            if segment_bytes:
                logger.debug(
                    "VAD 음성 세그먼트 감지",
                    job_id=self.job_id,
                    segment_bytes=len(segment_bytes)
                )
                return segment_bytes
            return None

        else:
            # --- WhisperLiveKit 모드: 버퍼 누적 ---
            self.audio_buffer.extend(audio_chunk)

            # 버퍼가 임계값을 넘으면 반환
            if len(self.audio_buffer) >= self.buffer_threshold:
                segment_bytes = bytes(self.audio_buffer)
                self.audio_buffer.clear()

                logger.debug(
                    "버퍼 임계값 도달, 세그먼트 반환",
                    job_id=self.job_id,
                    segment_bytes=len(segment_bytes)
                )
                return segment_bytes

            return None

    def flush_buffer(self) -> Optional[bytes]:
        """
        ✅ 연결 종료 시 남은 버퍼 강제 반환

        Returns:
            남은 버퍼 내용 (없으면 None)
        """
        if self.use_vad:
            # faster-whisper: VAD 버퍼 flush
            if self.vad_processor:
                return self.vad_processor.flush()
        else:
            # WhisperLiveKit: 오디오 버퍼 flush
            if len(self.audio_buffer) > 0:
                segment_bytes = bytes(self.audio_buffer)
                self.audio_buffer.clear()

                logger.debug(
                    "버퍼 flush",
                    job_id=self.job_id,
                    segment_bytes=len(segment_bytes)
                )
                return segment_bytes

        return None

    def get_full_transcript(self) -> str:
        """전체 대화록 반환"""
        return " ".join(self.full_transcript)