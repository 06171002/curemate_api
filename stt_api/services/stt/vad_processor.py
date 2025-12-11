
from stt_api.core.config import constants
import webrtcvad
from collections import deque
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import AudioFormatError

logger = get_logger(__name__)


class VADProcessor:
    """
    실시간 오디오 스트림을 받아 VAD로 음성/침묵을 감지하고,
    음성이 끝나는 시점에 전체 오디오 세그먼트를 반환합니다.
    """

    def __init__(self,
                 sample_rate=constants.VAD_SAMPLE_RATE,
                 frame_duration_ms=constants.VAD_FRAME_DURATION_MS,
                 vad_aggressiveness=constants.VAD_AGGRESSIVENESS,
                 min_speech_frames=constants.VAD_MIN_SPEECH_FRAMES,
                 max_silence_frames=constants.VAD_MAX_SILENCE_FRAMES):
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

        # ✅ 최적화된 파라미터
        self.min_speech_frames = min_speech_frames
        self.max_silence_frames = max_silence_frames

        self.speech_buffer = deque()
        self.in_speech = False
        self.silence_frames = 0
        self.speech_frames = 0  # ✅ 음성 프레임 카운터 추가

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
        if len(audio_chunk) != self.frame_bytes:
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
            self.speech_frames += 1
            self.silence_frames = 0

            # ✅ 최소 음성 길이 충족 시 in_speech 활성화
            if self.speech_frames >= self.min_speech_frames:
                self.in_speech = True
        else:
            # 침묵
            if self.in_speech:
                # 이전에 말을 하고 있었다면, 침묵 카운트 시작
                self.silence_frames += 1
                # ✅ 침묵이 짧으면 버퍼에 계속 추가 (끊김 방지)
                if self.silence_frames < self.max_silence_frames:
                    self.speech_buffer.append(audio_chunk)
                else:
                    # 세그먼트 종료
                    segment = b''.join(list(self.speech_buffer))
                    self._reset()
                    return segment
            else:
                # 음성 시작 전 침묵은 버리기
                self.speech_buffer.clear()

        return None  # "아직 세그먼트 종료 안됨"

    def _reset(self):
        """상태 초기화"""
        self.speech_buffer.clear()
        self.in_speech = False
        self.silence_frames = 0
        self.speech_frames = 0

    def flush(self):
        """
        ✅ 연결 종료 시 남은 버퍼 강제 반환
        """
        if len(self.speech_buffer) > 0 and self.speech_frames >= self.min_speech_frames:
            segment = b''.join(list(self.speech_buffer))
            self._reset()
            return segment
        return None