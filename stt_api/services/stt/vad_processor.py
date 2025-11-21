
from stt_api.core.config import constants
import webrtcvad
from collections import deque
from stt_api.core.logging_config import get_logger
from patient_api.core.exceptions import AudioFormatError

logger = get_logger(__name__)


class VADProcessor:
    """
    실시간 오디오 스트림을 받아 VAD로 음성/침묵을 감지하고,
    음성이 끝나는 시점에 전체 오디오 세그먼트를 반환합니다.
    """

    def __init__(self,
                 sample_rate=constants.VAD_SAMPLE_RATE,
                 frame_duration_ms=constants.VAD_FRAME_DURATION_MS,
                 vad_aggressiveness=constants.VAD_AGGRESSIVENESS):
        if sample_rate not in [8000, 16000, 32000, 48000]:
            # ✅ CustomException 사용
            raise AudioFormatError(
                expected="8000, 16000, 32000, 48000 Hz",
                actual=f"{sample_rate} Hz"
            )

        self.vad = webrtcvad.Vad(vad_aggressiveness)
        self.sample_rate = sample_rate

        # 30ms 프레임 (webrtcvad의 요구사항)
        self.frame_duration_ms = frame_duration_ms
        self.frame_bytes = int(sample_rate * (frame_duration_ms / 1000.0) * 2)  # 16-bit PCM

        self.speech_buffer = deque()
        self.in_speech = False
        self.silence_frames = 0
        self.max_silence_frames = 10  # 약 600ms (30ms * 20) 침묵 시 세그먼트 종료

        logger.debug(
            "VADProcessor 초기화",
            sample_rate=sample_rate,
            frame_bytes=self.frame_bytes
        )

    def process_chunk(self, audio_chunk: bytes):
        """
        오디오 청크(bytes)를 처리합니다.
        만약 음성 세그먼트가 종료되면, 해당 세그먼트(bytes)를 반환합니다.
        """

        # VAD는 정확히 'frame_bytes' 크기의 조각만 처리할 수 있습니다.
        # (클라이언트가 30ms 조각으로 보내야 함을 의미)
        if len(audio_chunk) != self.frame_bytes:
            # ✅ CustomException 사용 (또는 경고만 로그)
            logger.warning(
                "VAD 프레임 크기 불일치",
                expected=self.frame_bytes,
                actual=len(audio_chunk)
            )
            # 선택: 예외 발생 또는 None 반환
            raise AudioFormatError(
                expected=f"{self.frame_bytes} bytes",
                actual=f"{len(audio_chunk)} bytes"
            )


        is_speech = self.vad.is_speech(audio_chunk, self.sample_rate)

        if is_speech:
            # 말하고 있는 중
            self.speech_buffer.append(audio_chunk)
            self.in_speech = True
            self.silence_frames = 0
        else:
            # 침묵
            if self.in_speech:
                # 이전에 말을 하고 있었다면, 침묵 카운트 시작
                self.silence_frames += 1
                if self.silence_frames >= self.max_silence_frames:
                    # 침묵이 충분히 지속됨 -> 세그먼트 종료
                    self.in_speech = False
                    self.silence_frames = 0

                    segment = b''.join(list(self.speech_buffer))
                    self.speech_buffer.clear()
                    return segment  # "음성 세그먼트" 반환

        return None  # "아직 세그먼트 종료 안됨"