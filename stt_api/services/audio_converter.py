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
        is_streaming_format: bool = True
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

                # 2. 디코더 컨텍스트 생성
                try:
                    self.decoder = av.CodecContext.create(codec_name, "r")

                    # Opus 등 일부 코덱은 초기 설정 필요 (WebRTC 표준)
                    if codec_name == "opus":
                        self.decoder.sample_rate = 48000
                        self.decoder.channels = 2  # 보통 Stereo로 옴
                    elif codec_name == "pcm_s16le":
                        self.decoder.sample_rate = 48000
                        self.decoder.channels = 2

                except Exception as e:
                    logger.warning(f"PyAV 코덱 초기화 실패 ({codec_name}), 자동 감지 모드로 전환될 수 있음: {e}")
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
            # --- 1. 디코딩 ---
            decoded_frames = []

            if self.decoder:
                # PyAV Packet 생성
                packet = av.Packet(raw_audio_chunk)
                try:
                    # 패킷 디코딩 (프레임 리스트 반환)
                    decoded_frames = self.decoder.decode(packet)
                except Exception as e:
                    # 패킷 손상 등의 이유로 디코딩 실패 시 무시하고 진행
                    logger.debug("패킷 디코딩 실패 (무시됨)", error=str(e))
                    return []
            else:
                # 디코더가 없으면 Raw PCM으로 가정하고 바로 처리 시도 (예외적 상황)
                # (이 부분은 pydub 로직을 fallback으로 남겨둘 수도 있음)
                return []

            # --- 2. 리샘플링 및 버퍼링 ---
            for frame in decoded_frames:
                # 프레임을 타겟 포맷으로 리샘플링 (16k, mono, s16)
                resampled_frames = self.resampler.resample(frame)

                for resampled in resampled_frames:
                    # ndarray -> bytes 변환하여 버퍼에 추가
                    # PyAV 프레임은 numpy array로 변환 가능
                    pcm_bytes = resampled.to_ndarray().tobytes()
                    self.buffer.extend(pcm_bytes)

            # --- 3. 30ms 프레임 추출 ---
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