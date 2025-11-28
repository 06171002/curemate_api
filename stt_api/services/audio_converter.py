"""
오디오 스트림 변환 유틸리티 (PyAV 기반 개선 버전)

WebRTC에서 받은 Opus/PCM 패킷을 상태를 유지하며 VAD 요구사항으로 변환
"""

import av
import numpy as np
from typing import Optional, List
from stt_api.core.config import constants
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import AudioFormatError

logger = get_logger(__name__)


class AudioStreamConverter:
    """
    실시간 오디오 스트림을 VAD 요구사항(16kHz, 16-bit, Mono)으로 변환

    특징:
    - PyAV를 사용하여 디코딩 컨텍스트(CodecContext) 유지
    - 패킷 단위 디코딩으로 끊김 없는 스트리밍 처리
    - av.AudioResampler를 통한 고속 리샘플링
    """

    def __init__(
        self,
        target_sample_rate: int = constants.VAD_SAMPLE_RATE,
        target_frame_duration_ms: int = constants.VAD_FRAME_DURATION_MS,
        input_format: str = "opus",  # opus, pcm_s16le, mp3 등
        is_streaming_format: bool = True,
        input_sample_rate: int = 48000,
        input_channels: int = 2
    ):
        self.target_sample_rate = target_sample_rate
        self.target_frame_duration_ms = target_frame_duration_ms
        self.input_format = input_format
        self.is_streaming_format = is_streaming_format
        self.input_sample_rate = input_sample_rate  # 저장
        self.input_channels = input_channels

        # 목표 프레임 크기 계산 (바이트)
        # 16kHz * 30ms = 480 samples * 2 bytes = 960 bytes
        self.target_frame_bytes = int(
            target_sample_rate * (target_frame_duration_ms / 1000.0) * 2
        )

        # 내부 PCM 버퍼
        self.buffer = bytearray()

        # 통계
        self.total_received_bytes = 0
        self.total_output_frames = 0

        # --- PyAV 디코더 및 리샘플러 초기화 ---
        self.decoder = None
        self.resampler = None

        try:
            if self.is_streaming_format:
                # 1. 코덱 이름 매핑 (ffmpeg 코덱 이름 기준)
                codec_name = self.input_format
                if codec_name == "pcm":
                    codec_name = "pcm_s16le"  # WebRTC 기본 PCM

                # ✅ 수정: PCM인 경우 디코더를 생성하지 않고 넘어감 (Raw 데이터 처리)
                if codec_name == "pcm_s16le":
                    self.decoder = None
                    logger.info("Raw PCM 모드: 별도 디코더 없이 처리합니다.")
                else:
                    # Opus 등 다른 코덱은 디코더 생성
                    try:
                        self.decoder = av.CodecContext.create(codec_name, "r")
                        if codec_name == "opus":
                            self.decoder.sample_rate = 48000
                            self.decoder.channels = 2
                    except Exception as e:
                        logger.warning(f"PyAV 코덱 초기화 실패 ({codec_name}): {e}")
                        self.decoder = None

                # 3. 리샘플러 초기화 (입력 포맷은 첫 프레임 수신 시 설정됨)
                self.resampler = av.AudioResampler(
                    format='s16',            # 16-bit PCM
                    layout='mono',           # Mono
                    rate=self.target_sample_rate  # 16000Hz
                )

        except Exception as e:
            logger.error("AudioConverter 초기화 중 오류", exc_info=True, error=str(e))

        logger.info(
            "AudioStreamConverter(PyAV) 초기화",
            input_format=self.input_format,
            decoder=self.decoder.name if self.decoder else "None"
        )

    def convert_and_buffer(self, raw_audio_chunk: bytes) -> List[bytes]:
        """
        오디오 패킷을 디코딩하고 리샘플링하여 버퍼에 추가
        """
        if not raw_audio_chunk:
            return []

        self.total_received_bytes += len(raw_audio_chunk)

        try:
            decoded_frames = []

            # ✅ Case 1: Opus 등 코덱이 있는 경우 (기존 로직 유지)
            if self.decoder:
                packet = av.Packet(raw_audio_chunk)
                try:
                    decoded_frames = self.decoder.decode(packet)
                except Exception as e:
                    logger.debug("패킷 디코딩 실패 (무시됨)", error=str(e))
                    return []

            # ✅ Case 2: Raw PCM인 경우 (수정된 로직)
            elif self.input_format in ["pcm", "pcm_s16le", "raw"]:
                # 1. bytes -> numpy array (int16)
                array = np.frombuffer(raw_audio_chunk, dtype=np.int16)

                # 2. 차원 변환: (Samples, Channels) -> (Channels, Samples)
                # 예: 1440 샘플, 2채널인 경우
                # - 기존: (1440, 2) -> PyAV가 Packed 포맷에서 에러 발생
                # - 변경: (2, 1440) -> PyAV Planar 포맷(s16p)에 적합
                if self.input_channels > 0:
                    array = array.reshape(-1, self.input_channels).T
                    array = np.ascontiguousarray(array)
                else:
                    # 채널 정보가 없으면 1채널로 가정
                    array = array.reshape(1, -1)

                # 3. PyAV Frame 생성 (Planar 포맷 's16p' 사용)
                # s16p는 채널별로 데이터가 나뉘어 있는 포맷입니다.
                layout = 'stereo' if self.input_channels == 2 else 'mono'
                frame = av.AudioFrame.from_ndarray(
                    array,
                    format='s16p',  # ✅ s16 대신 s16p 사용
                    layout=layout
                )
                frame.sample_rate = self.input_sample_rate
                decoded_frames = [frame]

            else:
                return []

            # --- 3. 리샘플링 및 버퍼링 (기존과 동일) ---
            for frame in decoded_frames:
                # 프레임을 타겟 포맷(16k, mono, s16)으로 리샘플링
                resampled_frames = self.resampler.resample(frame)

                for resampled in resampled_frames:
                    pcm_bytes = resampled.to_ndarray().tobytes()
                    self.buffer.extend(pcm_bytes)

            return self._extract_frames()

        except Exception as e:
            logger.error("오디오 변환 오류", error=str(e))
            return []

    def _extract_frames(self) -> List[bytes]:
        """버퍼에서 30ms 단위로 프레임 추출"""
        frames = []
        while len(self.buffer) >= self.target_frame_bytes:
            frame = bytes(self.buffer[:self.target_frame_bytes])
            frames.append(frame)
            del self.buffer[:self.target_frame_bytes]
        return frames

    def flush(self) -> Optional[bytes]:
        """남은 데이터 처리"""
        # 스트리밍 모드에서는 디코더 내부 버퍼 flush가 필요할 수 있음
        if self.decoder:
            try:
                # 빈 패킷을 보내 내부 버퍼 비우기 (일부 코덱)
                # decoded_frames = self.decoder.decode(None)
                # ... 처리 로직 (생략 가능, VAD에서는 보통 무시됨)
                pass
            except:
                pass

        if len(self.buffer) > 0:
            remaining = bytes(self.buffer)
            self.buffer.clear()
            return remaining
        return None

    def get_stats(self) -> dict:
        return {
            "total_received_bytes": self.total_received_bytes,
            "total_output_frames": self.total_output_frames,
            "buffer_bytes": len(self.buffer)
        }