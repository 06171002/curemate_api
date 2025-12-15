# run-gpu.sh
#!/bin/bash

echo "🚀 GPU 모드로 실행합니다..."

# .env.gpu를 .env로 복사
cp .env.gpu .env

# Docker Compose 실행
docker-compose -f docker-compose.yml up --build -d

echo "✅ GPU 모드 실행 완료!"
echo "📋 로그 확인: docker-compose logs -f"