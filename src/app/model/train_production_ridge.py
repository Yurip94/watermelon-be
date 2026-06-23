"""
운영 Ridge 학습: labeled 전 구간(2020-01-31 ~ 2026-06-13)으로 재학습 후 아티팩트 저장.
"""
from pathlib import Path

import joblib
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.multioutput import MultiOutputRegressor
from sklearn.preprocessing import StandardScaler

BASE = Path("/Users/bell/sesac/수박이박수/수박 도소매가")
CSV = BASE / "watermelon_dataset_targets.csv"
ART = BASE / "ridge_production.joblib"

FEATURES = [
    "wholesale_price", "price_lag_1d", "price_lag_7d", "price_ma_7d", "price_ma_30d",
    "price_std_7d", "price_trend_7_30", "price_diff_7d",
    "temp_cum_30d", "avg_temp", "volume_lag_7d", "trade_volume", "is_peak_season",
    "month_sin", "month_cos",
]
TARGETS = [f"y_t{h}" for h in range(1, 8)]
WARMUP_ROWS = 30
ALPHA = 1.0


def main():
    df = pd.read_csv(CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True).iloc[WARMUP_ROWS:].reset_index(drop=True)

    labeled = df.dropna(subset=TARGETS).reset_index(drop=True)
    X = labeled[FEATURES].values
    Y = labeled[TARGETS].values

    print(f"학습 행: {len(labeled)}  "
          f"({labeled['date'].min().date()} ~ {labeled['date'].max().date()})")

    scaler = StandardScaler().fit(X)
    Xs = scaler.transform(X)
    model = MultiOutputRegressor(Ridge(alpha=ALPHA)).fit(Xs, Y)

    joblib.dump({
        "model": model,
        "scaler": scaler,
        "features": FEATURES,
        "targets": TARGETS,
        "alpha": ALPHA,
        "trained_through": str(labeled["date"].max().date()),
        "n_train_rows": len(labeled),
    }, ART)
    print(f"saved: {ART}")


if __name__ == "__main__":
    main()
