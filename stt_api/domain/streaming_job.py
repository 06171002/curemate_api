import uuid
from typing import List, Dict
from stt_api.services.stt.vad_processor import VADProcessor  #
from stt_api.core.config import constants
from stt_api.core.logging_config import get_logger

logger = get_logger(__name__)

class StreamingJob:
    """
    (F-JOB-01) 각 실시간 스트림의 고유 상태를 관리하는 클래스
    """

    def __init__(self, metadata: Dict = None):
        self.job_id: str = str(uuid.uuid4())
        self.metadata: Dict = metadata or {}

        # (★핵심) 각 Job은 고유의 VAD 인스턴스를 가집니다.
        # (클라이언트가 16kHz, 16-bit, 30ms 청크를 보낸다고 가정)
        self.vad_processor = VADProcessor(
            sample_rate=constants.VAD_SAMPLE_RATE,
            frame_duration_ms=constants.VAD_FRAME_DURATION_MS
        )

        # (★핵심) 이 Job만의 대화록과 문맥
        self.full_transcript: List[str] = []
        self.current_prompt_context: str = ""

        self.status: str = "processing"  # 인메모리 상태

        logger.info(
            "StreamingJob 생성 완료",
            job_id=self.job_id,
            vad_sample_rate=constants.VAD_SAMPLE_RATE,
            vad_frame_duration=constants.VAD_FRAME_DURATION_MS
        )

    def process_audio_chunk(self, audio_chunk: bytes):
        """
        이 Job의 VAD에 오디오 청크를 공급합니다.
        음성 세그먼트가 감지되면 True를 반환합니다. (테스트용)
        """
        segment_bytes = self.vad_processor.process_chunk(audio_chunk)
        if segment_bytes:
            logger.debug(
                "VAD 음성 세그먼트 감지",
                job_id=self.job_id,
                segment_bytes=len(segment_bytes)
            )
            # (나중에 여기에 STT 로직 추가)
            # (테스트를 위해 임시로 '감지됨' 텍스트 추가)
            self.full_transcript.append(f"[감지된 세그먼트: {len(segment_bytes)} bytes]")
            return segment_bytes
        return False

    def get_full_transcript(self) -> str:
        """이 Job의 전체 대화록을 문자열로 반환"""
        return " ".join(self.full_transcript)