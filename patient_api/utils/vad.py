

import webrtcvad
from collections import deque


class VADProcessor:
    """
    실시간 오디오 스트림을 받아 VAD로 음성/침묵을 감지하고,
    음성이 끝나는 시점에 전체 오디오 세그먼트를 반환합니다.
    """

    def __init__(self, sample_rate=16000, frame_duration_ms=30, vad_aggressiveness=3):
        if sample_rate not in [8000, 16000, 32000, 48000]:
            raise ValueError("VAD 지원 sample_rate는 8k, 16k, 32k, 48k 중 하나여야 합니다.")

        self.vad = webrtcvad.Vad(vad_aggressiveness)
        self.sample_rate = sample_rate

        # 30ms 프레임 (webrtcvad의 요구사항)
        self.frame_duration_ms = frame_duration_ms
        self.frame_bytes = int(sample_rate * (frame_duration_ms / 1000.0) * 2)  # 16-bit PCM

        self.speech_buffer = deque()
        self.in_speech = False
        self.silence_frames = 0
        self.max_silence_frames = 20  # 약 600ms (30ms * 20) 침묵 시 세그먼트 종료

    def process_chunk(self, audio_chunk: bytes):
        """
        오디오 청크(bytes)를 처리합니다.
        만약 음성 세그먼트가 종료되면, 해당 세그먼트(bytes)를 반환합니다.
        """

        # VAD는 정확히 'frame_bytes' 크기의 조각만 처리할 수 있습니다.
        # (클라이언트가 30ms 조각으로 보내야 함을 의미)
        if len(audio_chunk) != self.frame_bytes:
            print(f"오류: VAD는 정확히 {self.frame_bytes} 바이트의 청크만 처리할 수 있습니다.")
            return None

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