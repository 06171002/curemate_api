import asyncio
import sys
import shutil
import os
from whisperlivekit import TranscriptionEngine, AudioProcessor
import torch
import numpy as np
import random

# 시드 고정
def set_seed(seed=42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    # CPU 연산 결정성 보장
    torch.use_deterministic_algorithms(True, warn_only=True)

set_seed(42)



# ================= 설정 =================
AUDIO_FILE_PATH = "../temp_audio/test.mp3"
MODEL_SIZE = "large-v3"
LANGUAGE = "ko"
USE_DIARIZATION = False

# ========================================

def check_ffmpeg():
    print("🔍 FFmpeg 경로 확인 중...")
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        print(f"✅ FFmpeg 발견됨: {ffmpeg_path}")
        return True
    else:
        common_paths = [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        ]
        for path in common_paths:
            if os.path.exists(path):
                return True
        print("❌ 오류: FFmpeg를 찾을 수 없습니다!")
        return False


async def process_file_stream():
    if not check_ffmpeg(): return

    print(f"🔄 [초기화] WhisperLiveKit 엔진 로딩 중... (모델: {MODEL_SIZE}, CPU 모드)")

    try:
        engine = TranscriptionEngine(
            model=MODEL_SIZE,
            language=LANGUAGE,
            diarization=USE_DIARIZATION,
            backend="faster_whisper",
        )
        audio_processor = AudioProcessor(transcription_engine=engine)
        result_generator = await audio_processor.create_tasks()

    except Exception as e:
        print(f"❌ 엔진 초기화 실패: {e}")
        return

    print(f"📂 [파일 로드] {AUDIO_FILE_PATH}")

    try:
        if not os.path.exists(AUDIO_FILE_PATH):
            print(f"❌ 파일 없음: {AUDIO_FILE_PATH}")
            return

        # ★ 수정 1: 청크 크기를 작게 줄여서 '자주' 보냄 (연결 끊김 방지)
        CHUNK_SIZE = 4096

        print(f"▶️ [시작] MP3 파일 스트리밍 시작...")

        # 결과 출력 태스크
        async def print_results():
            try:
                async for res in result_generator:
                    try:
                        text = res.text.strip()
                        speaker = getattr(res, 'speaker', 'Unknown')
                        if text:
                            print(f"\n🗣️ [{speaker}] {text}")  # 줄바꿈 추가
                    except AttributeError:
                        pass
            except Exception:
                pass

        printer_task = asyncio.create_task(print_results())

        # 오디오 전송 루프
        with open(AUDIO_FILE_PATH, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break

                await audio_processor.process_audio(chunk)

                # ★ 수정 2: 대기 시간을 아주 짧게 (0.02초) 줄여서 끊김 없이 계속 보냄
                await asyncio.sleep(0.02)
                print(".", end="", flush=True)

        print("\n\n✅ [전송 완료] 데이터는 다 보냈습니다. 이제 밀린 변환을 기다립니다...")
        print("   (CPU 속도에 따라 시간이 걸릴 수 있습니다. 강제 종료하지 마세요!)")

        # ★ 수정 3: 남은 버퍼가 처리될 때까지 충분히 대기 (최대 60초)
        for i in range(60):
            if i % 5 == 0:
                print(f"   ⏳ 처리 중... ({i}초 경과)")
            await asyncio.sleep(1)

        printer_task.cancel()
        print("\n🏁 [종료] 테스트를 마칩니다.")

    except Exception as e:
        print(f"\n❌ 실행 중 오류 발생: {e}")


if __name__ == "__main__":
    asyncio.run(process_file_stream())