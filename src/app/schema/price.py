from datetime import date
from typing import Literal

from pydantic import BaseModel


class DatedPrice(BaseModel):
    date: date
    price: int


class SummaryPrices(BaseModel):
    yesterday: DatedPrice
    today: DatedPrice
    tomorrow: DatedPrice


class ForecastResponse(BaseModel):
    base_date: date
    unit: Literal["원/kg"] = "원/kg"
    summary_prices: SummaryPrices
    forecast: list[DatedPrice]
