from collections.abc import Generator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import prices as prices_endpoint
from app.db.base import Base
from app.db.models import ActualPrice, PricePrediction
from app.db.session import get_db
from app.main import app

FIXED_TODAY = date(2026, 6, 22)
BASE_DATE = FIXED_TODAY - timedelta(days=1)  # T = 어제


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed(session: Session) -> None:
    session.add(ActualPrice(date=BASE_DATE, actual_price=2436.0))
    prices_seq = [2384.0, 2314.0, 2264.0, 2294.0, 2354.0, 2334.0, 2204.0]
    prev = 2436.0
    for h, p in enumerate(prices_seq, start=1):
        session.add(
            PricePrediction(
                base_date=BASE_DATE,
                target_date=BASE_DATE + timedelta(days=h),
                predicted_price=p,
                price_diff=round(p - prev, 2),
            )
        )
        prev = p
    session.commit()


@pytest.fixture
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _seed(db_session)

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    monkeypatch.setattr(prices_endpoint, "_today", lambda: FIXED_TODAY)
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_forecast_matches_frontend_shape(client: TestClient) -> None:
    response = client.get("/api/v1/prices")
    assert response.status_code == 200
    body = response.json()

    assert body == {
        "base_date": "2026-06-21",
        "unit": "원/kg",
        "summary_prices": {
            "yesterday": {"date": "2026-06-21", "price": 2436},
            "today": {"date": "2026-06-22", "price": 2384},
            "tomorrow": {"date": "2026-06-23", "price": 2314},
        },
        "forecast": [
            {"date": "2026-06-22", "price": 2384},
            {"date": "2026-06-23", "price": 2314},
            {"date": "2026-06-24", "price": 2264},
            {"date": "2026-06-25", "price": 2294},
            {"date": "2026-06-26", "price": 2354},
            {"date": "2026-06-27", "price": 2334},
            {"date": "2026-06-28", "price": 2204},
        ],
    }


def test_forecast_returns_503_when_predictions_missing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(ActualPrice(date=BASE_DATE, actual_price=2436.0))
    db_session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    monkeypatch.setattr(prices_endpoint, "_today", lambda: FIXED_TODAY)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/api/v1/prices")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503


def test_forecast_yesterday_falls_back_to_latest_actual(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = BASE_DATE - timedelta(days=3)
    db_session.add(ActualPrice(date=older, actual_price=2100.0))
    prices_seq = [2384.0, 2314.0, 2264.0, 2294.0, 2354.0, 2334.0, 2204.0]
    prev = 2100.0
    for h, p in enumerate(prices_seq, start=1):
        db_session.add(
            PricePrediction(
                base_date=BASE_DATE,
                target_date=BASE_DATE + timedelta(days=h),
                predicted_price=p,
                price_diff=round(p - prev, 2),
            )
        )
        prev = p
    db_session.commit()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    monkeypatch.setattr(prices_endpoint, "_today", lambda: FIXED_TODAY)
    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.get("/api/v1/prices")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["summary_prices"]["yesterday"] == {
        "date": older.isoformat(),
        "price": 2100,
    }
