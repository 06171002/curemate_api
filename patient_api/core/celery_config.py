from celery import Celery
from patient_api.core.config import settings
from patient_api.core.config import constants

# 1. 브로커 URL 설정 (job_manager.py와 동일한 Redis 서버)
# 0번 DB를 Celery가 메시지 큐로 사용합니다.
BROKER_URL = settings.REDIS_URL

# 2. Celery 앱 생성
# 'worker'는 Celery가 작업(@celery_app.task)을 스캔할 파일 이름입니다.
celery_app = Celery(
    'curemate_tasks',
    broker=BROKER_URL,
    include=['patient_api.services.tasks']  # 'tasks.py' 파일에서 작업을 스캔하라는 의미
)

# 3. (중요) 결과 백엔드 설정 안 함!
# 우리는 Celery의 기본 결과 저장소를 사용하지 않습니다.
# 대신, 우리가 직접 만든
# 'transcribed', 'completed' 상태를 직접 DB에 저장할 것입니다.
# 이것이 훨씬 더 유연하고 강력합니다.

# (선택) 시간대 설정
celery_app.conf.timezone = constants.CELERY_TIMEZONE