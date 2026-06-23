import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.init_db import init_db
from app.db.models import PricePrediction
from app.db.session import SessionLocal
from app.scheduler import build_scheduler
from app.service.prediction_writer import run_daily_prediction

logger = logging.getLogger(__name__)


def _bootstrap_predictions_if_empty() -> None:
    with SessionLocal() as db:
        exists = db.execute(select(PricePrediction.id).limit(1)).first()
    if exists:
        return
    try:
        run_daily_prediction()
    except Exception:
        logger.exception("bootstrap prediction failed; will retry on cron")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap_predictions_if_empty()
    scheduler = build_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {"message": f"{settings.app_name} is running"}

    return app


app = create_app()
