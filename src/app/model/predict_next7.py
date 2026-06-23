"""
추론: 가장 최근 채워진 행(T)으로 T+1~T+7 도매가 예측.
"""
from pathlib import Path

import joblib
import pandas as pd

BASE = Path("/Users/bell/sesac/수박이박수/수박 도소매가")
CSV = BASE / "watermelon_dataset_targets.csv"
ART = BASE / "ridge_production.joblib"


def main():
    art = joblib.load(ART)
    model, scaler, FEATURES = art["model"], art["scaler"], art["features"]
    print(f"모델 학습 기간 종료일: {art['trained_through']}  alpha={art['alpha']}")

    df = pd.read_csv(CSV)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # T = 가격이 채워진 마지막 날
    T = df.loc[df["wholesale_price"].notna(), "date"].max()
    row = df.loc[df["date"] == T]
    x = row[FEATURES].values
    today_price = float(row["wholesale_price"].iloc[0])
    print(f"T = {T.date()}  (오늘 도매가 = {today_price:.1f}원)")

    preds = model.predict(scaler.transform(x))[0]  # (7,)

    out = pd.DataFrame({
        "base_date": [T.date()] * 7,
        "target_date": [(T + pd.Timedelta(days=h)).date() for h in range(1, 8)],
        "horizon": list(range(1, 8)),
        "predicted_price": preds.round(1),
    })

    out_path = BASE / f"predictions_T{T.date()}.csv"
    out.to_csv(out_path, index=False)

    print("\n=== 예측 결과 ===")
    print(f"{'base_date':<12} {'target_date':<12} {'h':>3} {'pred(원)':>10}  {'Δ(어제대비)':>12}")
    prev = today_price
    for _, r in out.iterrows():
        delta = r["predicted_price"] - prev
        print(f"{str(r['base_date']):<12} {str(r['target_date']):<12} "
              f"{r['horizon']:>3} {r['predicted_price']:>10.1f}  {delta:>+11.1f}")
        prev = r["predicted_price"]

    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    main()
