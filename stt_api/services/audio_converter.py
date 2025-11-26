"""
오디오 스트림 변환 유틸리티

WebRTC에서 받은 원본 오디오를 VAD 요구사항에 맞게 변환
"""

import numpy as np
from typing import Optional, Tuple
from io import BytesIO
from pydub import AudioSegment
from stt_api.core.config import constants
from stt_api.core.logging_config import get_logger
from stt_api.core.exceptions import AudioFormatError

logger = get_logger(__name__)


class AudioStreamConverter:
    """
    실시간 오디오 스트림을 VAD 요구사항(16kHz, 16-bit, Mono)으로 변환

    특징:
    - 누적 버퍼 방식으로 청크 단위 처리
    - 다양한 입력 포맷 지원 (WebRTC Opus, PCM 등)
    - 메모리 효율적인 스트리밍 처리
    """

    def __init__(
        self,
        target_sample_rate: int = constants.VAD_SAMPLE_RATE,
        target_frame_duration_ms: int = constants.VAD_FRAME_DURATION_MS,
        input_format: str = "opus",  # opus, pcm, webm 등
        is_streaming_format: bool = True  # False면 전체 파일 필요 (mp3 등)
    ):
        self.target_sample_rate = target_sample_rate
        self.target_frame_duration_ms = target_frame_duration_ms
        self.input_format = input_format
        self.is_streaming_format = is_streaming_format

        # 목표 프레임 크기 계산 (바이트)
        # 16kHz * 30ms = 480 samples * 2 bytes = 960 bytes
        self.target_frame_bytes = int(
            target_sample_rate * (target_frame_duration_ms / 1000.0) * 2
        )

        # 내부 버퍼 (변환된 PCM 데이터 누적)
        self.buffer = bytearray()

        # ✅ 스트리밍 불가능한 포맷용 원본 버퍼 (MP3 등)
        self.raw_buffer = bytearray() if not is_streaming_format else None

        # 통계
        self.total_received_bytes = 0
        self.total_output_frames = 0

        logger.info(
            "AudioStreamConverter 초기화",
            target_sample_rate=target_sample_rate,
            target_frame_bytes=self.target_frame_bytes,
            input_format=input_format,
            is_streaming_format=is_streaming_format
        )

    def convert_and_buffer(self, raw_audio_chunk: bytes) -> list[bytes]:
        """
        원본 오디오 청크를 변환하고 버퍼에 추가

        Args:
            raw_audio_chunk: WebRTC에서 받은 원본 오디오 바이트

        Returns:
            변환된 30ms 프레임 리스트 (여러 개 반환 가능)

        Raises:
            AudioFormatError: 오디오 변환 실패 시
        """
        if not raw_audio_chunk:
            return []

        self.total_received_bytes += len(raw_audio_chunk)

        # ✅ MP3 등 스트리밍 불가능한 포맷은 원본 버퍼에 누적만
        if not self.is_streaming_format:
            self.raw_buffer.extend(raw_audio_chunk)
            return []  # 프레임은 flush() 시점에 반환

        try:
            # 1. 원본 오디오를 AudioSegment로 변환
            audio = self._decode_audio_chunk(raw_audio_chunk)

            # 2. VAD 요구사항에 맞게 리샘플링 (16kHz, Mono, 16-bit)
            audio = audio.set_frame_rate(self.target_sample_rate)
            audio = audio.set_channels(1)  # Mono
            audio = audio.set_sample_width(2)  # 16-bit

            # 3. PCM 데이터를 버퍼에 추가
            pcm_data = audio.raw_data
            self.buffer.extend(pcm_data)

            # 4. 버퍼에서 30ms 프레임들을 추출
            frames = self._extract_frames()

            if frames:
                self.total_output_frames += len(frames)
                logger.debug(
                    "오디오 변환 완료",
                    input_bytes=len(raw_audio_chunk),
                    output_frames=len(frames),
                    buffer_bytes=len(self.buffer)
                )

            return frames

        except Exception as e:
            logger.error(
                "오디오 변환 실패",
                exc_info=True,
                input_size=len(raw_audio_chunk),
                error=str(e)
            )
            # ✅ 스트리밍 포맷 실패 시 경고만 출력하고 계속 진행
            logger.warning("청크 변환 실패, 다음 청크로 계속 진행")
            return []

    def _decode_audio_chunk(self, raw_chunk: bytes) -> AudioSegment:
        """
        원본 오디오 청크를 AudioSegment로 디코딩

        WebRTC는 보통 Opus 코덱을 사용하지만,
        다양한 포맷에 대응하기 위해 자동 감지 시도
        """
        try:
            # 1. 지정된 포맷으로 디코딩 시도
            if self.input_format == "opus":
                return AudioSegment.from_file(
                    BytesIO(raw_chunk),
                    format="ogg",
                    codec="opus"
                )
            elif self.input_format == "pcm":
                # 이미 PCM인 경우 (일부 WebRTC 구현)
                # 샘플레이트와 채널 정보가 필요 - 일반적으로 48kHz Stereo
                return AudioSegment(
                    data=raw_chunk,
                    sample_width=2,  # 16-bit
                    frame_rate=48000,  # WebRTC 기본값
                    channels=2  # Stereo
                )
            else:
                # 기타 포맷 (mp3, webm 등)
                return AudioSegment.from_file(
                    BytesIO(raw_chunk),
                    format=self.input_format
                )

        except Exception as e:
            logger.warning(
                f"{self.input_format} 디코딩 실패, 자동 감지 시도",
                error=str(e)
            )

            # 2. 자동 포맷 감지 (폴백)
            try:
                return AudioSegment.from_file(BytesIO(raw_chunk))
            except Exception as auto_error:
                raise AudioFormatError(
                    expected=f"{self.input_format} or auto-detectable format",
                    actual=f"Decode failed: {str(auto_error)}"
                )

    def _extract_frames(self) -> list[bytes]:
        """
        버퍼에서 30ms 프레임들을 추출

        Returns:
            추출된 프레임 리스트
        """
        frames = []

        while len(self.buffer) >= self.target_frame_bytes:
            # 정확히 target_frame_bytes만큼 추출
            frame = bytes(self.buffer[:self.target_frame_bytes])
            frames.append(frame)

            # 버퍼에서 제거
            del self.buffer[:self.target_frame_bytes]

        return frames

    def flush(self) -> Optional[bytes]:
        """
        버퍼에 남은 데이터를 마지막 프레임으로 반환

        ✅ MP3 등 비스트리밍 포맷의 경우 전체 변환 수행

        Returns:
            남은 데이터 또는 변환된 프레임들
        """
        # ✅ 비스트리밍 포맷: 전체 원본 버퍼를 한 번에 변환
        if not self.is_streaming_format and len(self.raw_buffer) > 0:
            logger.info(
                "비스트리밍 포맷 전체 변환 시작",
                format=self.input_format,
                total_bytes=len(self.raw_buffer)
            )

            try:
                # 전체 파일 디코딩
                audio = AudioSegment.from_file(
                    BytesIO(bytes(self.raw_buffer)),
                    format=self.input_format
                )

                # 리샘플링
                audio = audio.set_frame_rate(self.target_sample_rate)
                audio = audio.set_channels(1)
                audio = audio.set_sample_width(2)

                # PCM 데이터 추출
                pcm_data = audio.raw_data
                self.buffer.extend(pcm_data)
                self.raw_buffer.clear()

                logger.info(
                    "전체 변환 완료",
                    pcm_bytes=len(pcm_data),
                    duration_sec=len(pcm_data) / (self.target_sample_rate * 2)
                )

            except Exception as e:
                logger.error(
                    "전체 파일 변환 실패",
                    exc_info=True,
                    error=str(e)
                )
                return None

        # 스트리밍 포맷 또는 변환 완료 후: 남은 버퍼 반환
        if len(self.buffer) > 0:
            remaining = bytes(self.buffer)
            self.buffer.clear()

            logger.debug(
                "버퍼 flush",
                remaining_bytes=len(remaining)
            )

            return remaining

        return None

    def get_stats(self) -> dict:
        """변환 통계 반환"""
        return {
            "total_received_bytes": self.total_received_bytes,
            "total_output_frames": self.total_output_frames,
            "buffer_bytes": len(self.buffer),
            "target_frame_bytes": self.target_frame_bytes
        }


class SimpleResampler:
    """
    간단한 리샘플러 (pydub 없이 NumPy만 사용)

    WebRTC에서 이미 PCM 형태로 데이터가 오는 경우 사용
    """

    def __init__(
        self,
        input_sample_rate: int = 48000,
        output_sample_rate: int = 16000,
        input_channels: int = 2
    ):
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.input_channels = input_channels
        self.resample_ratio = output_sample_rate / input_sample_rate

        logger.info(
            "SimpleResampler 초기화",
            input_rate=input_sample_rate,
            output_rate=output_sample_rate,
            ratio=self.resample_ratio
        )

    def resample_chunk(self, pcm_data: bytes) -> bytes:
        """
        PCM 데이터를 리샘플링

        Args:
            pcm_data: 16-bit PCM 데이터

        Returns:
            리샘플링된 16-bit PCM 데이터
        """
        # 1. bytes → numpy array
        audio_np = np.frombuffer(pcm_data, dtype=np.int16)

        # 2. Stereo → Mono (채널 평균)
        if self.input_channels == 2:
            audio_np = audio_np.reshape(-1, 2).mean(axis=1).astype(np.int16)

        # 3. 리샘플링 (선형 보간)
        num_samples = int(len(audio_np) * self.resample_ratio)
        resampled = np.interp(
            np.linspace(0, len(audio_np) - 1, num_samples),
            np.arange(len(audio_np)),
            audio_np
        ).astype(np.int16)

        # 4. numpy array → bytes
        return resampled.tobytes()