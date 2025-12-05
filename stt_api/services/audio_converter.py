"""
최적화된 하이브리드 AudioStreamConverter
"""

import av
import numpy as np
from typing import Optional, List
from io import BytesIO
from pydub import AudioSegment

from stt_api.core.config import constants
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import AudioFormatError

logger = get_logger(__name__)


class AudioStreamConverter:
    """
    WebRTC 스트림 변환기 (하이브리드 방식)

    전략:
    - Raw PCM: NumPy 직접 처리 (가장 빠름)
    - Opus/WebM: PyAV 스트리밍 디코딩 (안정적)
    - MP3/AAC: Pydub 일괄 처리 (호환성)
    """

    def __init__(
        self,
        target_sample_rate: int = constants.VAD_SAMPLE_RATE,
        target_frame_duration_ms: int = constants.VAD_FRAME_DURATION_MS,
        input_format: str = "opus",
        is_streaming_format: bool = True,
        input_sample_rate: int = 48000,
        input_channels: int = 2
    ):
        self.target_sample_rate = target_sample_rate
        self.target_frame_duration_ms = target_frame_duration_ms
        self.input_format = input_format.lower()
        self.is_streaming_format = is_streaming_format
        self.input_sample_rate = input_sample_rate
        self.input_channels = input_channels

        # 목표 프레임 크기
        self.target_frame_bytes = int(
            target_sample_rate * (target_frame_duration_ms / 1000.0) * 2
        )

        # 내부 버퍼
        self.buffer = bytearray()
        self.raw_buffer = bytearray() if not is_streaming_format else None

        # 통계
        self.total_received_bytes = 0
        self.total_output_frames = 0

        # ✅ 전략 선택
        self.strategy = self._select_strategy()

        # ✅ PyAV 디코더 (Opus/WebM용)
        self.decoder = None
        self.resampler = None

        if self.strategy == "pyav":
            self._init_pyav()

        logger.info(
            "AudioStreamConverter 초기화",
            strategy=self.strategy,
            input_format=input_format,
            target_sample_rate=target_sample_rate
        )

    def _select_strategy(self) -> str:
        """
        입력 포맷에 따라 최적 전략 선택

        Returns:
            "numpy" | "pyav" | "pydub"
        """
        # 1. Raw PCM → NumPy 직접 처리 (가장 빠름)
        if self.input_format in ["pcm", "pcm_s16le", "raw"]:
            return "numpy"

        # 2. Opus/WebM → PyAV 스트리밍 (안정적)
        if self.input_format in ["opus", "webm"] and self.is_streaming_format:
            return "pyav"

        # 3. 기타/MP3 → Pydub 폴백 (호환성)
        return "pydub"

    def _init_pyav(self):
        """PyAV 디코더 초기화 (Opus/WebM용)"""
        try:
            # ✅ [핵심 수정] Opus는 Parser를 통해 처리해야 함
            if self.input_format == "opus":
                # Opus 디코더 생성 (옵션 없이)
                self.decoder = av.CodecContext.create("libopus", "r")

            else:  # webm
                codec_name = "vp8"
                self.decoder = av.CodecContext.create(codec_name, "r")

            # 리샘플러 생성
            self.resampler = av.AudioResampler(
                format='s16',
                layout='mono',
                rate=self.target_sample_rate
            )

            logger.info("PyAV 디코더 초기화 완료", codec=self.input_format)

        except Exception as e:
            logger.warning(f"PyAV 초기화 실패, Pydub로 폴백: {e}")
            self.strategy = "pydub"

    def convert_and_buffer(self, raw_audio_chunk: bytes) -> List[bytes]:
        """
        오디오 청크 변환

        전략별 분기 처리
        """
        if not raw_audio_chunk:
            return []

        self.total_received_bytes += len(raw_audio_chunk)

        # 비스트리밍 포맷: 버퍼에만 누적
        if not self.is_streaming_format:
            self.raw_buffer.extend(raw_audio_chunk)
            return []

        try:
            # ✅ 전략별 처리
            if self.strategy == "numpy":
                return self._process_numpy(raw_audio_chunk)
            elif self.strategy == "pyav":
                return self._process_pyav(raw_audio_chunk)
            else:  # pydub
                return self._process_pydub(raw_audio_chunk)

        except Exception as e:
            logger.error("오디오 변환 오류", error=str(e), strategy=self.strategy)
            return []

    def _process_numpy(self, raw_chunk: bytes) -> List[bytes]:
        """
        ✅ NumPy 직접 처리 (Raw PCM)

        가장 빠른 방식 - FFmpeg 없이 순수 NumPy
        """
        # 1. bytes → numpy array
        audio_np = np.frombuffer(raw_chunk, dtype=np.int16)

        # 2. Stereo → Mono (채널 평균)
        if self.input_channels == 2:
            audio_np = audio_np.reshape(-1, 2)
            audio_np = audio_np.mean(axis=1).astype(np.int16)

        # 3. 리샘플링 (선형 보간)
        if self.input_sample_rate != self.target_sample_rate:
            ratio = self.target_sample_rate / self.input_sample_rate
            num_samples = int(len(audio_np) * ratio)

            audio_np = np.interp(
                np.linspace(0, len(audio_np) - 1, num_samples),
                np.arange(len(audio_np)),
                audio_np
            ).astype(np.int16)

        # 4. 버퍼에 추가
        pcm_bytes = audio_np.tobytes()
        self.buffer.extend(pcm_bytes)

        return self._extract_frames()

    def _process_pyav(self, raw_chunk: bytes) -> List[bytes]:
        """
        ✅ PyAV 스트리밍 처리 (Opus/WebM)

        안정적이고 효율적
        """
        try:
            # 패킷 디코딩
            packet = av.Packet(raw_chunk)
            frames = self.decoder.decode(packet)

            # 리샘플링
            for frame in frames:
                resampled_frames = self.resampler.resample(frame)

                for resampled in resampled_frames:
                    pcm_bytes = resampled.to_ndarray().tobytes()
                    self.buffer.extend(pcm_bytes)

            return self._extract_frames()

        except Exception as e:
            logger.debug("PyAV 디코딩 실패", error=str(e))
            return []

    def _process_pydub(self, raw_chunk: bytes) -> List[bytes]:
        """
        ✅ Pydub 처리 (폴백/호환성)

        느리지만 안정적
        """
        try:
            # AudioSegment 디코딩
            audio = AudioSegment.from_file(
                BytesIO(raw_chunk),
                format=self.input_format
            )

            # 리샘플링
            audio = audio.set_frame_rate(self.target_sample_rate)
            audio = audio.set_channels(1)
            audio = audio.set_sample_width(2)

            # 버퍼에 추가
            pcm_data = audio.raw_data
            self.buffer.extend(pcm_data)

            return self._extract_frames()

        except Exception as e:
            logger.warning("Pydub 처리 실패", error=str(e))
            return []

    def _extract_frames(self) -> List[bytes]:
        """버퍼에서 30ms 프레임 추출"""
        frames = []

        while len(self.buffer) >= self.target_frame_bytes:
            frame = bytes(self.buffer[:self.target_frame_bytes])
            frames.append(frame)
            del self.buffer[:self.target_frame_bytes]
            self.total_output_frames += 1

        return frames

    def flush(self) -> Optional[bytes]:
        """버퍼 flush"""
        # 비스트리밍 포맷: 전체 변환
        if not self.is_streaming_format and len(self.raw_buffer) > 0:
            try:
                audio = AudioSegment.from_file(
                    BytesIO(bytes(self.raw_buffer)),
                    format=self.input_format
                )

                audio = audio.set_frame_rate(self.target_sample_rate)
                audio = audio.set_channels(1)
                audio = audio.set_sample_width(2)

                self.buffer.extend(audio.raw_data)
                self.raw_buffer.clear()

                logger.info("비스트리밍 포맷 전체 변환 완료")

            except Exception as e:
                logger.error("전체 변환 실패", error=str(e))

        # 남은 버퍼 반환
        if len(self.buffer) > 0:
            remaining = bytes(self.buffer)
            self.buffer.clear()
            return remaining

        return None

    def get_stats(self) -> dict:
        return {
            "strategy": self.strategy,
            "total_received_bytes": self.total_received_bytes,
            "total_output_frames": self.total_output_frames,
            "buffer_bytes": len(self.buffer)
        }