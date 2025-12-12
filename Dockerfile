# Dockerfile

# 1. 베이스 이미지 (Python 3.12 Slim 버전)
# FROM python:3.12-slim

# 2. 작업 디렉터리 설정
# WORKDIR /app

# 3. (★중요) requirements.txt를 먼저 복사하여 설치
#    (이러면 나중에 코드만 수정해도 라이브러리를 다시 설치하지 않아 빠름)
# COPY stt_api/requirements.txt .

# 4. 라이브러리 설치
#    (webrtcvad는 C++ 빌드가 필요할 수 있어 build-essential 설치)
# RUN apt-get update && apt-get install -y build-essential ffmpeg
# RUN pip install --no-cache-dir -r requirements.txt

# 5. 나머지 프로젝트 파일 전체 복사
# COPY . .

# (CMD 명령어는 docker-compose.yml에서 따로 지정할 것이므로 여기서는 생략)

# 1. 베이스 이미지 변경 (NVIDIA CUDA 12.4 + cuDNN 9 + Ubuntu 22.04)
# 이 이미지는 GPU 드라이버와 cuDNN 9 라이브러리를 포함하고 있습니다.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

# 2. 환경 설정 (상호작용 방지)
ENV DEBIAN_FRONTEND=noninteractive

# 3. Python 3.12 및 필수 패키지 설치
# Ubuntu 22.04 기본 파이썬은 3.10이므로 ppa를 통해 3.12를 설치합니다.
RUN apt-get update && apt-get install -y \
    software-properties-common \
    wget \
    git \
    ffmpeg \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    distutils \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.12 /usr/bin/python

# 4. pip 설치 (get-pip.py 사용)
RUN wget https://bootstrap.pypa.io/get-pip.py && python get-pip.py

# 5. 작업 디렉터리 설정
WORKDIR /app

# 6. requirements.txt 복사 및 설치
COPY stt_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 7. 프로젝트 파일 복사
COPY . .

# 8. (중요) LD_LIBRARY_PATH 확인 (NVIDIA 이미지는 기본 설정되어 있으나 확실하게 명시)
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/cuda/lib64:/usr/lib/x86_64-linux-gnu