# run-cpu.sh
#!/bin/bash

echo "🖥️  CPU 모드로 실행합니다..."

# .env.cpu를 .env로 복사
cp .env.cpu .env

# Docker Compose 실행
docker-compose -f docker-compose.cpu.yml up --build -d

echo "✅ CPU 모드 실행 완료!"
echo "📋 로그 확인: docker-compose -f docker-compose.cpu.yml logs -f"