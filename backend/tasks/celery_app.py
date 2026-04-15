"""Celery application with beat schedule for weekly scraping."""
from celery import Celery
from celery.schedules import crontab
from config import settings

app = Celery("trend_tracker")

app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "tasks.scrape_tasks.*": {"queue": "scrape"},
        "tasks.analysis_tasks.*": {"queue": "analysis"},
        "tasks.aldi_tasks.*": {"queue": "aldi"},
        "tasks.instore_tasks.*": {"queue": "aldi"},
        "tasks.report_tasks.*": {"queue": "reports"},
    },
    beat_schedule={
        # Every 10 minutes — reset any products stuck in RUNNING and re-queue them
        "reset-stuck-analyses": {
            "task": "tasks.analysis_tasks.reset_stuck_analyses",
            "schedule": crontab(minute="*/10"),
        },
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

# Explicitly include task modules so Celery registers them on startup
app.conf.update(include=["tasks.scrape_tasks", "tasks.analysis_tasks", "tasks.aldi_tasks", "tasks.instore_tasks"])
