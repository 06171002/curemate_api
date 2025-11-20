# Dockerfile

# 1. 베이스 이미지 (Python 3.12 Slim 버전)
FROM python:3.12-slim

# 2. 작업 디렉터리 설정
WORKDIR /app

# 3. (★중요) requirements.txt를 먼저 복사하여 설치
#    (이러면 나중에 코드만 수정해도 라이브러리를 다시 설치하지 않아 빠름)
COPY stt_api/requirements.txt .

# 4. 라이브러리 설치
#    (webrtcvad는 C++ 빌드가 필요할 수 있어 build-essential 설치)
RUN apt-get update && apt-get install -y build-essential
RUN pip install --no-cache-dir -r requirements.txt

# 5. 나머지 프로젝트 파일 전체 복사
COPY . .

# (CMD 명령어는 docker-compose.yml에서 따로 지정할 것이므로 여기서는 생략)