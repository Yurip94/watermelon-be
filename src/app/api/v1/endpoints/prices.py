from datetime import date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ActualPrice, PricePrediction
from app.db.session import get_db
from app.schema.price import DatedPrice, ForecastResponse, SummaryPrices

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]


def _today() -> date:
    return datetime.now(tz=ZoneInfo(settings.prediction_timezone)).date()


@router.get("", response_model=ForecastResponse)
def get_forecast(db: DatabaseSession) -> ForecastResponse:
    today = _today()

    rows = db.execute(
        select(PricePrediction)
        .where(PricePrediction.target_date >= today)
        .order_by(PricePrediction.target_date.asc())
        .limit(7)
    ).scalars().all()

    if len(rows) < 7:
        raise HTTPException(
            status_code=503,
            detail="forecast not ready: insufficient predictions for today",
        )

    forecast = [
        DatedPrice(date=r.target_date, price=int(round(float(r.predicted_price))))
        for r in rows
    ]
    base_date = rows[0].base_date  # 일반적으로 today - 1

    yesterday_date = today - timedelta(days=1)
    actual = db.execute(
        select(ActualPrice).where(ActualPrice.date == yesterday_date)
    ).scalar_one_or_none()
    if actual is None:
        actual = db.execute(
            select(ActualPrice).order_by(ActualPrice.date.desc()).limit(1)
        ).scalar_one_or_none()
    if actual is None:
        raise HTTPException(status_code=503, detail="actual price unavailable")

    yesterday = DatedPrice(
        date=actual.date,
        price=int(round(float(actual.actual_price))),
    )

    return ForecastResponse(
        base_date=base_date,
        summary_prices=SummaryPrices(
            yesterday=yesterday,
            today=forecast[0],
            tomorrow=forecast[1],
        ),
        forecast=forecast,
    )
