#
# from stt_api.core.config import constants
# import webrtcvad
# from collections import deque
# from stt_api.core.logging_config import get_logger
# from stt_api.core.exceptions import AudioFormatError
# import numpy as np
# from scipy.signal import butter, lfilter
#
# logger = get_logger(__name__)
#
#
# class VADProcessor:
#     """
#     실시간 오디오 스트림을 받아 VAD로 음성/침묵을 감지하고,
#     음성이 끝나는 시점에 전체 오디오 세그먼트를 반환합니다.
#     """
#
#     def __init__(self,
#                  sample_rate=constants.VAD_SAMPLE_RATE,
#                  frame_duration_ms=constants.VAD_FRAME_DURATION_MS,
#                  vad_aggressiveness=constants.VAD_AGGRESSIVENESS,
#                  min_speech_frames=constants.VAD_MIN_SPEECH_FRAMES,
#                  max_silence_frames=constants.VAD_MAX_SILENCE_FRAMES):
#         if sample_rate not in [8000, 16000, 32000, 48000]:
#             # ✅ CustomException 사용
#             raise AudioFormatError(
#                 expected="8000, 16000, 32000, 48000 Hz",
#                 actual=f"{sample_rate} Hz"
#             )
#
#         self.vad = webrtcvad.Vad(vad_aggressiveness)
#         self.sample_rate = sample_rate
#
#         # 30ms 프레임 (webrtcvad의 요구사항)
#         self.frame_duration_ms = frame_duration_ms
#         self.frame_bytes = int(sample_rate * (frame_duration_ms / 1000.0) * 2)  # 16-bit PCM
#
#         # ✅ 최적화된 파라미터
#         self.min_speech_frames = min_speech_frames
#         self.max_silence_frames = max_silence_frames
#
#         self.speech_buffer = deque()
#         self.in_speech = False
#         self.silence_frames = 0
#         self.speech_frames = 0  # ✅ 음성 프레임 카운터 추가
#
#         self.energy_threshold = 300
#
#         logger.debug(
#             "VADProcessor 초기화",
#             sample_rate=sample_rate,
#             frame_bytes=self.frame_bytes
#         )
#
#
#     def process_chunk(self, audio_chunk: bytes):
#         """
#         오디오 청크(bytes)를 처리합니다.
#         만약 음성 세그먼트가 종료되면, 해당 세그먼트(bytes)를 반환합니다.
#         """
#         if len(audio_chunk) != self.frame_bytes:
#             logger.warning(
#                 "VAD 프레임 크기 불일치",
#                 expected=self.frame_bytes,
#                 actual=len(audio_chunk)
#             )
#             # 선택: 예외 발생 또는 None 반환
#             raise AudioFormatError(
#                 expected=f"{self.frame_bytes} bytes",
#                 actual=f"{len(audio_chunk)} bytes"
#             )
#
#         is_speech = self.vad.is_speech(audio_chunk, self.sample_rate)
#
#         if is_speech:
#             # bytes -> numpy int16 변환
#             audio_data = np.frombuffer(audio_chunk, dtype=np.int16)
#
#             # RMS(Root Mean Square) 에너지 계산
#             rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
#
#             # 소리가 너무 작으면 침묵으로 강제 변경
#             if rms < self.energy_threshold:
#                 is_speech = False
#                 # (선택) 로그를 찍어 적절한 임계값을 찾으세요
#                 logger.debug(f"소음 차단됨: RMS {rms:.2f}")
#
#         if is_speech:
#             # 말하고 있는 중
#             self.speech_buffer.append(audio_chunk)
#             self.speech_frames += 1
#             self.silence_frames = 0
#
#             # ✅ 최소 음성 길이 충족 시 in_speech 활성화
#             if self.speech_frames >= self.min_speech_frames:
#                 self.in_speech = True
#         else:
#             # 침묵
#             if self.in_speech:
#                 # 이전에 말을 하고 있었다면, 침묵 카운트 시작
#                 self.silence_frames += 1
#                 # ✅ 침묵이 짧으면 버퍼에 계속 추가 (끊김 방지)
#                 if self.silence_frames < self.max_silence_frames:
#                     self.speech_buffer.append(audio_chunk)
#                 else:
#                     # 세그먼트 종료
#                     segment = b''.join(list(self.speech_buffer))
#                     self._reset()
#                     return segment
#             else:
#                 # 음성 시작 전 침묵은 버리기
#                 self.speech_buffer.clear()
#
#         return None  # "아직 세그먼트 종료 안됨"
#
#     def _reset(self):
#         """상태 초기화"""
#         self.speech_buffer.clear()
#         self.in_speech = False
#         self.silence_frames = 0
#         self.speech_frames = 0
#
#     def flush(self):
#         """
#         ✅ 연결 종료 시 남은 버퍼 강제 반환
#         """
#         if len(self.speech_buffer) > 0 and self.speech_frames >= self.min_speech_frames:
#             segment = b''.join(list(self.speech_buffer))
#             self._reset()
#             return segment
#         return None

import torch
import numpy as np
from collections import deque
from stt_api.core.config import constants, settings
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import AudioFormatError

logger = get_logger(__name__)


class VADProcessor:
    """
    Silero VAD 기반의 실시간 음성 감지 프로세서
    """

    def __init__(self):
        # 1. 설정 로드
        self.sample_rate = constants.VAD_SAMPLE_RATE
        self.frame_duration_ms = constants.VAD_FRAME_DURATION_MS
        self.threshold = constants.VAD_THRESHOLD

        # 32ms @ 16kHz = 512 samples * 2 bytes = 1024 bytes
        self.frame_bytes = int(self.sample_rate * (self.frame_duration_ms / 1000.0) * 2)

        # 2. Silero VAD 모델 로드 (Torch Hub 사용)
        logger.info("Silero VAD 모델 로드 중...")
        try:
            # 로컬에 캐시된 모델이 있으면 그것을 사용, 없으면 다운로드
            self.model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=False  # CPU 추론용 (ONNX 사용 시 True)
            )
            self.get_speech_timestamps, _, _, _, _ = utils
            logger.info("Silero VAD 모델 로드 완료")

        except Exception as e:
            logger.error(f"Silero VAD 로드 실패: {e}")
            raise e

        # 3. 상태 관리 변수
        self.min_speech_frames = constants.VAD_MIN_SPEECH_FRAMES
        self.max_silence_frames = constants.VAD_MAX_SILENCE_FRAMES

        self.speech_buffer = deque()
        self.in_speech = False
        self.silence_frames = 0
        self.speech_frames = 0

        # ✅ Silero VAD는 상태(State)를 가짐 (RNN 구조)
        self.model.reset_states()

    def process_chunk(self, audio_chunk: bytes):
        """
        오디오 청크를 받아 음성 구간(Segment)이 끝나면 반환
        """
        # 1. 청크 크기 검증
        if len(audio_chunk) != self.frame_bytes:
            # 마지막 자투리 패킷 등은 무시하거나 패딩 처리
            return None

        # 2. Int16 Bytes -> Float32 Tensor 변환 (Silero 입력 포맷)
        # (1) bytes -> numpy int16
        audio_int16 = np.frombuffer(audio_chunk, np.int16)

        # (2) int16 -> float32 (정규화: -1.0 ~ 1.0)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        # (3) numpy -> torch tensor
        audio_tensor = torch.from_numpy(audio_float32)

        # 3. 모델 추론 (음성일 확률 계산)
        # model(input, sample_rate) -> probability (0.0 ~ 1.0)
        with torch.no_grad():
            speech_prob = self.model(audio_tensor, self.sample_rate).item()

        # 4. 확률 기반 음성 판별
        is_speech = speech_prob >= self.threshold

        # --- 기존 버퍼링 로직 유지 (Silero에 맞게 적용) ---
        if is_speech:
            self.speech_buffer.append(audio_chunk)
            self.speech_frames += 1
            self.silence_frames = 0

            if self.speech_frames >= self.min_speech_frames:
                self.in_speech = True
        else:
            # 침묵 구간
            if self.in_speech:
                self.silence_frames += 1
                if self.silence_frames < self.max_silence_frames:
                    # 짧은 침묵은 말 끊김 방지를 위해 버퍼에 포함
                    self.speech_buffer.append(audio_chunk)
                else:
                    # 침묵이 길어지면 문장 종료로 판단 -> 세그먼트 반환
                    segment = b''.join(list(self.speech_buffer))
                    self._reset()
                    return segment
            else:
                # 말하기 시작 전의 침묵은 버림 (메모리 절약)
                self.speech_buffer.clear()
                self.speech_frames = 0

        return None

    def _reset(self):
        """세그먼트 반환 후 상태 초기화"""
        self.speech_buffer.clear()
        self.in_speech = False
        self.silence_frames = 0
        self.speech_frames = 0
        # ✅ 모델 내부 상태도 초기화하는 것이 좋음 (문맥 끊김 처리)
        # 단, 계속 이어지는 대화라면 초기화하지 않는 전략도 가능
        self.model.reset_states()

    def flush(self):
        """연결 종료 시 남은 버퍼 반환"""
        if len(self.speech_buffer) > 0 and self.speech_frames >= self.min_speech_frames:
            segment = b''.join(list(self.speech_buffer))
            self._reset()
            return segment
        return None