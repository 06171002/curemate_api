from faster_whisper import WhisperModel
import sys
import os
from typing import Optional

# --- 1. 모델 설정 (F-STT-01 세부사항) ---

# (설정) 사용할 모델 크기. (e.g., "tiny", "base", "medium", "large-v3")
# "medium"이 한국어에 적절한 속도와 품질을 제공합니다.
# "large-v3"는 가장 정확하지만, GPU가 없으면 매우 느립니다.
STT_MODEL_SIZE = "medium"

# (설정) GPU 사용 여부. "cuda" (NVIDIA GPU), "cpu"
# Apple Silicon (M1/M2/M3) 사용 시: "mps" (아직 faster-whisper에서 공식 지원X, 'auto' 권장)
# 'auto'로 두면 사용 가능한 장치를 자동 감지합니다.
DEVICE_TYPE = "auto"
COMPUTE_TYPE = "default"  # GPU의 경우 "float16", CPU의 경우 "int8" 권장

# --- 2. 모델 미리 로드 (F-STT-01 세부사항) ---

# 전역 변수로 모델을 저장하여, 서버 시작 시 1회만 로드되도록 합니다.
_model: Optional[WhisperModel] = None


def load_stt_model():
    """
    FastAPI 서버 시작 시 호출되어 STT 모델을 전역 변수(_model)에 미리 로드합니다.
    """
    global _model
    if _model is not None:
        print("[STT Service] 🟢 STT 모델이 이미 로드되었습니다.")
        return

    print(f"[STT Service] 🟡 STT 모델 로드를 시작합니다 (Model: {STT_MODEL_SIZE})...")

    try:
        # compute_type을 설정하면 더 최적화된 속도로 실행됩니다.
        # 예: GPU 사용 시 compute_type="float16"
        # 예: CPU 사용 시 compute_type="int8"

        _model = WhisperModel(
            STT_MODEL_SIZE,
            device=DEVICE_TYPE,
            compute_type=COMPUTE_TYPE
        )
        print(f"[STT Service] 🟢 STT 모델 로드 완료 (Device: {DEVICE_TYPE}, Compute: {COMPUTE_TYPE}).")

    except Exception as e:
        print(f"[STT Service] 🔴 STT 모델 로드 실패: {e}", file=sys.stderr)
        print("[STT Service] 🔴 CTranslate2/CUDA/PyTorch 설정을 확인하거나 모델 파일 다운로드에 실패했을 수 있습니다.", file=sys.stderr)
        _model = None  # 로드 실패


# --- 3. 핵심 기능: 오디오 변환 함수 ---

def transcribe_audio(file_path: str) -> str:
    """
    업로드된 임시 오디오 파일의 경로를 받아 텍스트로 변환합니다.
    (F-STT-01: VAD, 한국어 설정)
    """
    global _model
    # (★수정) Lazy Loading: 모델이 없으면 지금 로드!
    if not _model:
        # FastAPI 서버는 lifespan에서 이미 로드했겠지만,
        # Celery 워커는 여기서 처음 로드하게 됩니다.
        print("[STT Service] 🔴 모델이 로드되지 않았습니다. 지금 로드를 시도합니다...")
        load_stt_model()  #

        # 다시 한번 확인
        if not _model:
            # 로드에 또 실패했으면 에러 발생
            print("[STT Service] 🔴 STT 모델 로드에 실패했습니다. 워커 로그를 확인하세요.", file=sys.stderr)
            raise RuntimeError("STT 모델 로드에 실패했습니다. 워커 로그를 확인하세요.")

    print(f"[STT Service] 🔵 STT 작업을 시작합니다: {file_path}")

    try:
        # (F-STT-01) VAD 필터, 한국어 설정 적용
        segments, info = _model.transcribe(
            file_path,
            language="ko",  # 한국어 고정
            vad_filter=True,  # VAD(음성 구간 감지) 활성화
            vad_parameters={"min_silence_duration_ms": 500}
        )

        # 'segments'는 제너레이터(iterator)입니다.
        # 각 세그먼트의 텍스트를 하나로 합칩니다.
        transcript_parts = []
        for segment in segments:
            # segment.text.strip() -> 앞뒤 공백 제거
            transcript_parts.append(segment.text.strip())

        full_transcript = " ".join(transcript_parts)

        print(f"[STT Service] 🟢 STT 작업 완료 (감지된 언어: {info.language}, {info.language_probability:.2f})")
        return full_transcript

    except Exception as e:
        print(f"[STT Service] 🔴 STT 작업 중 오류 발생: {e}", file=sys.stderr)
        # 예외를 다시 발생시켜 worker.py에서 이 예외를 잡고,
        # job_manager를 통해 상태를 'failed'로 업데이트하도록 합니다.
        raise e


# (★신규) SSE를 위한 스트리밍(제너레이터) 버전
def transcribe_audio_streaming(file_path: str):
    """
    (SSE용)
    오디오 파일을 STT 처리하고, VAD가 감지한 '세그먼트'를
    즉시 'yield' (반환)합니다.
    """
    global _model
    if not _model:
        print("[STT Service] 🔴 모델이 로드되지 않았습니다. 지금 로드를 시도합니다...")
        load_stt_model()
        if not _model:
            raise RuntimeError("STT 모델 로드에 실패했습니다. 워커 로그를 확인하세요.")

    print(f"[STT Service] 🔵 (Streaming) STT 작업을 시작합니다: {file_path}")

    # (★중요) _model.transcribe 자체가 제너레이터입니다.
    segments, info = _model.transcribe(
        file_path,
        language="ko",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}
    )

    full_transcript_parts = []
    for segment in segments:
        segment_text = segment.text.strip()
        if segment_text:
            print(f"[STT Service] (Streaming) 🎤 세그먼트 감지: {segment_text}")
            full_transcript_parts.append(segment_text)
            # (★핵심) 감지된 세그먼트를 즉시 yield
            yield segment_text

            # (★핵심) 모든 STT가 끝나면, 전체 텍스트를 반환
    yield " ".join(full_transcript_parts)