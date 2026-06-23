"""일일 예측 적재: CSV -> 모델 -> price_predictions upsert."""
from __future__ import annotations

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import PricePrediction
from app.db.session import SessionLocal
from app.model.predictor import PredictionBundle, predict_next7

logger = logging.getLogger(__name__)


def _upsert(session: Session, bundle: PredictionBundle) -> None:
    rows = [
        {
            "base_date": bundle.base_date,
            "target_date": h.target_date,
            "predicted_price": h.predicted_price,
            "price_diff": h.price_diff,
        }
        for h in bundle.horizons
    ]
    stmt = pg_insert(PricePrediction).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_prediction_base_target",
        set_={
            "predicted_price": stmt.excluded.predicted_price,
            "price_diff": stmt.excluded.price_diff,
        },
    )
    session.execute(stmt)


def run_daily_prediction(session: Session | None = None) -> PredictionBundle:
    owned = session is None
    db = session or SessionLocal()
    try:
        bundle = predict_next7()
        _upsert(db, bundle)
        db.commit()
        logger.info(
            "predictions upserted: base_date=%s rows=%d",
            bundle.base_date,
            len(bundle.horizons),
        )
        return bundle
    except Exception:
        db.rollback()
        logger.exception("run_daily_prediction failed")
        raise
    finally:
        if owned:
            db.close()


__all__ = ["run_daily_prediction"]
