import time
from datetime import date, datetime, timedelta
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit_logger, get_client_ip
from app.core.config import settings
from app.db.models import ActualPrice, PricePrediction
from app.db.session import get_db
from app.schema.price import DatedPrice, ForecastResponse, SummaryPrices

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]


def _today() -> date:
    return datetime.now(tz=ZoneInfo(settings.prediction_timezone)).date()


@router.get("", response_model=ForecastResponse)
def get_forecast(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: DatabaseSession,
) -> ForecastResponse:
    request_id = uuid4().hex
    response.headers["X-Request-ID"] = request_id
    start = time.perf_counter()
    status_code = 200
    response_summary: dict | None = None
    try:
        result = _build_forecast(db)
        response_summary = {
            "base_date": result.base_date.isoformat(),
            "forecast": [
                {"date": f.date.isoformat(), "price": f.price}
                for f in result.forecast
            ],
        }
        return result
    except HTTPException as exc:
        status_code = exc.status_code
        response_summary = {"detail": exc.detail}
        raise
    except Exception:
        status_code = 500
        raise
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        background_tasks.add_task(
            audit_logger.log_event,
            {
                "request_id": request_id,
                "client_ip": get_client_ip(request),
                "user_agent": request.headers.get("user-agent", ""),
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "response_summary": response_summary,
            },
        )


def _build_forecast(db: Session) -> ForecastResponse:
    today = _today()
    yesterday_date = today - timedelta(days=1)
    tomorrow_date = today + timedelta(days=1)

    latest_base = db.execute(
        select(PricePrediction.base_date)
        .where(PricePrediction.target_date >= today)
        .order_by(PricePrediction.base_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_base is None:
        raise HTTPException(
            status_code=503,
            detail="forecast not ready: no predictions available",
        )

    rows = db.execute(
        select(PricePrediction)
        .where(
            PricePrediction.base_date == latest_base,
            PricePrediction.target_date >= today,
        )
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
    by_date = {f.date: f for f in forecast}

    if today not in by_date or tomorrow_date not in by_date:
        raise HTTPException(
            status_code=503,
            detail="forecast not ready: missing today/tomorrow prediction",
        )

    actual = db.execute(
        select(ActualPrice).where(ActualPrice.date == yesterday_date)
    ).scalar_one_or_none()
    if actual is not None:
        yesterday = DatedPrice(
            date=actual.date,
            price=int(round(float(actual.actual_price))),
        )
    else:
        yesterday_pred = db.execute(
            select(PricePrediction)
            .where(PricePrediction.target_date == yesterday_date)
            .order_by(PricePrediction.base_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if yesterday_pred is None:
            raise HTTPException(
                status_code=503,
                detail="yesterday price unavailable",
            )
        yesterday = DatedPrice(
            date=yesterday_date,
            price=int(round(float(yesterday_pred.predicted_price))),
        )

    return ForecastResponse(
        base_date=today,
        summary_prices=SummaryPrices(
            yesterday=yesterday,
            today=by_date[today],
            tomorrow=by_date[tomorrow_date],
        ),
        forecast=forecast,
    )
