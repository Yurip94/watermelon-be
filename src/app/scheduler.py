"""앱 컨테이너 내부 APScheduler: 매일 1회 예측 적재."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.service.prediction_writer import run_daily_prediction

logger = logging.getLogger(__name__)

_JOB_ID = "daily_prediction"


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.prediction_timezone)
    scheduler.add_job(
        run_daily_prediction,
        trigger=CronTrigger(
            hour=settings.prediction_cron_hour,
            minute=settings.prediction_cron_minute,
            timezone=settings.prediction_timezone,
        ),
        id=_JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler


__all__ = ["build_scheduler"]
