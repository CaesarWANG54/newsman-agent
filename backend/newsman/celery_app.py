from celery import Celery
from celery.schedules import crontab

from newsman.config import get_settings

settings = get_settings()

celery_app = Celery(
    "newsman",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["newsman.tasks"],
)

celery_app.conf.timezone = settings.daily_timezone
celery_app.conf.beat_schedule = {}

if settings.daily_update_enabled:
    celery_app.conf.beat_schedule["newsman-daily-ai-brief"] = {
        "task": "newsman.tasks.run_daily_digest",
        "schedule": crontab(
            hour=settings.daily_update_hour,
            minute=settings.daily_update_minute,
        ),
    }
