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
        "tasks.report_tasks.*": {"queue": "reports"},
    },
    beat_schedule={
        # Every Sunday at 01:00 UTC — trigger all scrapers
        "weekly-scrape-all": {
            "task": "tasks.scrape_tasks.scrape_all_retailers",
            "schedule": crontab(hour=1, minute=0, day_of_week=0),
        },
        # Every Sunday at 08:00 UTC — run trend analysis after scrape + analysis
        "weekly-trend-analysis": {
            "task": "tasks.analysis_tasks.run_trend_analysis",
            "schedule": crontab(hour=8, minute=0, day_of_week=0),
        },
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)

# Explicitly include task modules so Celery registers them on startup
app.conf.update(include=["tasks.scrape_tasks", "tasks.analysis_tasks", "tasks.aldi_tasks"])
