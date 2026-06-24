"""
데이터 파이프라인 스케줄러.

매일 정해진 시각(기본: 한국시간 새벽 3시)에 run_pipeline.main()을 실행한다.
설정은 config의 scheduler_timezone / scheduler_hour / scheduler_minute.

실행:
    uv run python -m app.pipeline.scheduler   # 상주 프로세스로 스케줄 대기

서버를 상주시키지 않는 환경이라면 OS cron 사용을 권장 (README/docs 참고):
    0 3 * * *  cd /path/to/watermelon-backend && \
        uv run python -m app.pipeline.run_pipeline
"""

from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.pipeline.run_pipeline import main as run_pipeline_main


def run_job() -> None:
    started = datetime.now(tz=None)
    print(f"\n[스케줄러] 파이프라인 시작: {started.isoformat(timespec='seconds')}")
    try:
        code = run_pipeline_main()
        print(f"[스케줄러] 파이프라인 종료 (exit={code})")
    except Exception as exc:  # 스케줄러가 죽지 않도록 예외를 잡아 로깅만
        print(f"[스케줄러] 파이프라인 예외: {exc}")


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    trigger = CronTrigger(
        hour=settings.scheduler_hour,
        minute=settings.scheduler_minute,
        timezone=settings.scheduler_timezone,
    )
    scheduler.add_job(
        run_job,
        trigger=trigger,
        id="daily_data_pipeline",
        name="수박 데이터 파이프라인 (매일)",
        misfire_grace_time=3600,   # 지연 시작 허용(최대 1시간)
        coalesce=True,             # 밀린 실행은 1회로 합침
        max_instances=1,           # 중복 실행 방지
    )
    return scheduler


def main() -> None:
    scheduler = build_scheduler()
    tz = settings.scheduler_timezone
    h, m = settings.scheduler_hour, settings.scheduler_minute
    print(
        f"[스케줄러] 매일 {h:02d}:{m:02d} ({tz}) 실행 예약됨. "
        f"대기 중... (Ctrl+C 종료)"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n[스케줄러] 종료.")


if __name__ == "__main__":
    main()
