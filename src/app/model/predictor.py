"""Ridge 모델로 가장 최근 가격이 있는 행(T)을 입력 삼아 T+1~T+7 도매가를 예측."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import IO, Union

import joblib
import pandas as pd
from azure.storage.blob import BlobClient

from app.core.config import settings


@dataclass(frozen=True)
class HorizonPrediction:
    target_date: date
    predicted_price: float
    price_diff: float  # 직전 target 대비(첫 항목은 today_price 대비)


@dataclass(frozen=True)
class PredictionBundle:
    base_date: date  # T (마지막으로 가격이 채워진 날)
    today_price: float
    horizons: list[HorizonPrediction]  # 길이 7


CsvSource = Union[str, Path, IO[bytes], IO[str]]


def _load_csv_from_blob() -> BytesIO:
    if not settings.blob_storage_url:
        raise RuntimeError("BLOB_STORAGE_URL 미설정")
    if not settings.blob_storage_access_key:
        raise RuntimeError("BLOB_STORAGE_ACCESS_KEY 미설정")
    account_url = settings.blob_storage_url.rstrip("/")
    client = BlobClient(
        account_url=account_url,
        container_name=settings.blob_container_name,
        blob_name=settings.blob_dataset_blob,
        credential=settings.blob_storage_access_key,
    )
    buf = BytesIO()
    client.download_blob().readinto(buf)
    buf.seek(0)
    return buf


def _read_csv(source: CsvSource | None) -> pd.DataFrame:
    if source is None:
        source = _load_csv_from_blob()
    df = pd.read_csv(source)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def predict_next7(csv_source: CsvSource | None = None) -> PredictionBundle:
    """CSV 마지막 가격행으로 7일치를 예측해 반환."""
    art = joblib.load(settings.model_artifact_path)
    model, scaler, features = art["model"], art["scaler"], art["features"]

    df = _read_csv(csv_source)
    last_t = df.loc[df["wholesale_price"].notna(), "date"].max()
    row = df.loc[df["date"] == last_t]
    x = row[features].values
    today_price = float(row["wholesale_price"].iloc[0])

    preds = model.predict(scaler.transform(x))[0]  # shape (7,)
    base = last_t.date()

    horizons: list[HorizonPrediction] = []
    prev = today_price
    for h, p in enumerate(preds, start=1):
        target = base + timedelta(days=h)
        price = float(p)
        horizons.append(
            HorizonPrediction(
                target_date=target,
                predicted_price=round(price, 2),
                price_diff=round(price - prev, 2),
            )
        )
        prev = price

    return PredictionBundle(base_date=base, today_price=today_price, horizons=horizons)


__all__ = ["HorizonPrediction", "PredictionBundle", "predict_next7"]
