"""
수집된 원천 데이터(temp_*.csv)를 마스터 데이터셋에 병합.

마스터(watermelon_dataset_targets.csv)는 41컬럼이며 cpi 컬럼을 포함하지 않는다.
도매가(wholesale_price)는 CPI 물가보정된 실질가격:
    wholesale_price = raw_price × (기준CPI / 그달CPI)
    - 기준CPI = 최신 발표월 CPI (= 현재 기준; 매달 갱신되면 전체 재환산)

원본가(raw_price)와 월별 CPI는 sidecar(price_raw.csv)에 함께 보관한다:
    date, raw_price, cpi   (cpi는 그 달 원본 CPI를 일별로 매핑)

실행 모드:
  - 매일(run_pipeline, refresh_cpi=False): sidecar의 기존 cpi 사용, 새 날만 추가.
    최신 CPI가 안 바뀌므로 과거 wholesale 불변(=가벼운 추가).
  - 매월(update_cpi, refresh_cpi=True): KOSIS 새 CPI를 sidecar cpi에 재매핑 →
    기준CPI 갱신 → 전체 wholesale 재환산(리스케일).
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from app.core.config import settings

# 일별 원천 입력 소스 (도매가는 CPI 보정 때문에 별도 처리)
DAILY_SOURCES = {
    "oil": ["oil_gasoline", "oil_diesel"],
    "volume": ["trade_volume"],
    "weather": [
        "avg_temp", "max_temp", "min_temp", "humidity",
        "sunshine_hours", "solar_radiation", "precipitation",
    ],
}

# 마스터 컬럼 순서 (41컬럼, cpi 미포함). date는 인덱스.
MASTER_COLUMNS = [
    "wholesale_price", "trade_volume", "sunshine_hours", "solar_radiation",
    "avg_temp", "max_temp", "min_temp", "precipitation", "humidity",
    "oil_gasoline", "oil_diesel", "month", "week", "dayofweek", "is_peak_season",
    "sunshine_cum_30d", "sunshine_cum_60d", "sunshine_cum_90d", "temp_cum_30d",
    "price_lag_1d", "price_lag_7d", "volume_lag_7d", "oil_ma_7d",
    "price_ma_3d", "price_ma_7d", "price_ma_14d", "price_ma_30d", "price_std_7d",
    "price_diff_1d", "price_diff_7d", "price_trend_7_30",
    "y_t1", "y_t2", "y_t3", "y_t4", "y_t5", "y_t6", "y_t7",
    "month_sin", "month_cos",
]


def _apply_daily_source(df_target, temp_path, columns):
    """temp CSV의 날짜별 값을 마스터에 갱신(기존행) + 추가(신규행)."""
    if not os.path.exists(temp_path):
        print(f"  [경고] {temp_path} 없음 — 건너뜀")
        return df_target

    src = pd.read_csv(temp_path)
    if src.empty:
        print(f"  [정보] {temp_path} 비어있음 — 건너뜀")
        return df_target

    src["date"] = pd.to_datetime(src["date"]).dt.strftime("%Y-%m-%d")
    src = src.set_index("date")
    df_target = df_target.reindex(df_target.index.union(src.index))

    for col in columns:
        if col not in src.columns:
            continue
        values = pd.to_numeric(src[col], errors="coerce")
        df_target.loc[values.index, col] = values.values

    print(f"  병합: {os.path.basename(temp_path)} → {columns} ({len(src)}일)")
    return df_target


def _build_price_from_sidecar(
    df_target, temp_price_path, temp_cpi_path, price_raw_path, refresh_cpi
):
    """sidecar(raw_price, cpi) 기반으로 wholesale_price(실질가) 산출.

    wholesale_price = raw_price × (기준CPI / 그달CPI), 기준CPI = 최신월 CPI.
    sidecar가 없으면 마스터 도매가에서 raw를 역산(부트스트랩).
    refresh_cpi=True면 KOSIS(temp_cpi)로 cpi를 재매핑(월간 갱신용).
    """
    dates = list(df_target.index)

    # 1. 기존 sidecar
    side_raw, side_cpi = {}, {}
    if price_raw_path and os.path.exists(price_raw_path):
        sdf = pd.read_csv(price_raw_path)
        sdf["date"] = pd.to_datetime(sdf["date"]).dt.strftime("%Y-%m-%d")
        raw_col = "raw_price" if "raw_price" in sdf.columns else "raw"
        side_raw = dict(zip(sdf["date"],
                            pd.to_numeric(sdf[raw_col], errors="coerce")))
        if "cpi" in sdf.columns:
            side_cpi = dict(zip(sdf["date"],
                                pd.to_numeric(sdf["cpi"], errors="coerce")))

    # 2. KOSIS 월별 CPI
    monthly = {}
    if temp_cpi_path and os.path.exists(temp_cpi_path):
        cdf = pd.read_csv(temp_cpi_path)
        monthly = dict(zip(cdf["date"].astype(str),
                           pd.to_numeric(cdf["cpi"], errors="coerce")))

    # 3. 날짜별 cpi 결정 (refresh_cpi면 KOSIS 우선, 아니면 sidecar 우선)
    cpi_vals = {}
    for d in dates:
        m = d[:7]
        if refresh_cpi and m in monthly:
            cpi_vals[d] = monthly[m]
        elif d in side_cpi and pd.notna(side_cpi[d]):
            cpi_vals[d] = side_cpi[d]
        elif m in monthly:
            cpi_vals[d] = monthly[m]
    cpi_ser = (
        pd.Series({d: cpi_vals.get(d, np.nan) for d in dates})
        .sort_index().ffill().bfill()
    )
    if cpi_ser.isna().all():
        print("  [경고] CPI 정보 없음 — 도매가 재환산 건너뜀")
        return df_target

    # 기준 CPI: 수동 고정값 우선, 아니면 최신(가장 최근 날짜) CPI
    if settings.cpi_base and settings.cpi_base > 0:
        cpi_base = float(settings.cpi_base)
    else:
        cpi_base = float(cpi_ser.sort_index().iloc[-1])

    # 4. 날짜별 raw_price 결정
    new_price = {}
    if temp_price_path and os.path.exists(temp_price_path):
        tp = pd.read_csv(temp_price_path)
        if not tp.empty:
            tp["date"] = pd.to_datetime(tp["date"]).dt.strftime("%Y-%m-%d")
            new_price = dict(zip(tp["date"],
                                 pd.to_numeric(tp["wholesale_price"], errors="coerce")))

    wp_master = pd.to_numeric(df_target.get("wholesale_price"), errors="coerce")
    raw_vals = {}
    for d in dates:
        if d in new_price and pd.notna(new_price[d]):
            raw_vals[d] = float(new_price[d])                      # 신규 수집 원본가
        elif d in side_raw and pd.notna(side_raw[d]):
            raw_vals[d] = float(side_raw[d])                       # 기존 sidecar
        elif d in wp_master.index and pd.notna(wp_master.get(d)):
            raw_vals[d] = wp_master[d] * cpi_ser[d] / cpi_base     # 부트스트랩 역산
    raw_ser = (
        pd.Series({d: raw_vals.get(d, np.nan) for d in dates})
        .sort_index()
        .interpolate(method="linear", limit_area="inside")        # 휴장일 보간
        .ffill().bfill()
    )

    # 5. wholesale = raw × (기준CPI / 그달CPI)
    wholesale = (raw_ser * cpi_base / cpi_ser).round(1)
    df_target["wholesale_price"] = wholesale.reindex(df_target.index).to_numpy()

    # 6. sidecar 저장 (date, raw_price, cpi)
    if price_raw_path:
        pd.DataFrame({
            "date": dates,
            "raw_price": [round(float(raw_ser[d]), 4) for d in dates],
            "cpi": [round(float(cpi_ser[d]), 4) for d in dates],
        }).to_csv(price_raw_path, index=False, encoding="utf-8")

    print(
        f"  도매가 재계산: {len(dates)}일 "
        f"(기준 CPI={cpi_base}, refresh_cpi={refresh_cpi})"
    )
    return df_target


def _fill_market_gaps(df_target):
    """휴장일(일/공휴일) 반입량 결측 채우기 (도매가는 sidecar에서 이미 보간됨).

    영업일 사이 닫힌 구간은 선형 보간, 마지막 열린 구간은 ffill.
    """
    for col in ["wholesale_price", "trade_volume"]:
        if col not in df_target.columns:
            continue
        s = pd.to_numeric(df_target[col], errors="coerce")
        s = s.interpolate(method="linear", limit_area="inside").ffill()
        df_target[col] = s.round(1)
    return df_target


def compute_derived_features(df):
    """검증된 공식으로 파생 피처 전체 재계산 (마스터 역검증 100% 일치 기준).

    이동평균 당일 포함/제외가 컬럼마다 다른 것은 원본 데이터 그대로 재현:
      - ma_7d, ma_30d : 당일 포함
      - ma_3d, ma_14d, std_7d, diff_*, trend : 당일 제외(shift 1)
    """
    idx = pd.to_datetime(df.index)

    # --- 달력 ---
    df["month"] = idx.month
    df["week"] = idx.isocalendar().week.to_numpy()
    df["dayofweek"] = idx.dayofweek
    df["is_peak_season"] = idx.month.isin([5, 6, 7, 8]).astype(int)
    df["month_sin"] = np.sin(2 * np.pi * idx.month / 12)
    df["month_cos"] = np.cos(2 * np.pi * idx.month / 12)

    # --- 도매가 파생 ---
    if "wholesale_price" in df.columns:
        wp = pd.to_numeric(df["wholesale_price"], errors="coerce")
        df["price_lag_1d"] = wp.shift(1)
        df["price_lag_7d"] = wp.shift(7)
        df["price_ma_7d"] = wp.rolling(7, min_periods=1).mean()    # 당일 포함
        df["price_ma_30d"] = wp.rolling(30, min_periods=1).mean()  # 당일 포함
        wp_e = wp.shift(1)  # 당일 제외 기준
        df["price_ma_3d"] = wp_e.rolling(3, min_periods=1).mean()
        df["price_ma_14d"] = wp_e.rolling(14, min_periods=1).mean()
        df["price_std_7d"] = wp_e.rolling(7, min_periods=1).std()  # ddof=1
        df["price_diff_1d"] = wp.shift(1) - wp.shift(2)
        df["price_diff_7d"] = wp.shift(1) - wp.shift(8)
        ma7_e = wp_e.rolling(7, min_periods=1).mean()
        ma30_e = wp_e.rolling(30, min_periods=1).mean()
        df["price_trend_7_30"] = ma7_e - ma30_e
        for k in range(1, 8):
            df[f"y_t{k}"] = wp.shift(-k)

    # --- 반입량 파생 ---
    if "trade_volume" in df.columns:
        vol = pd.to_numeric(df["trade_volume"], errors="coerce")
        df["volume_lag_7d"] = vol.shift(7)

    # --- 유가 파생 ---
    if "oil_gasoline" in df.columns:
        oil = pd.to_numeric(df["oil_gasoline"], errors="coerce")
        df["oil_ma_7d"] = oil.rolling(7, min_periods=1).mean()

    # --- 누적 기상 ---
    if "sunshine_hours" in df.columns:
        sh = pd.to_numeric(df["sunshine_hours"], errors="coerce")
        df["sunshine_cum_30d"] = sh.rolling(30, min_periods=1).sum()
        df["sunshine_cum_60d"] = sh.rolling(60, min_periods=1).sum()
        df["sunshine_cum_90d"] = sh.rolling(90, min_periods=1).sum()
    if "avg_temp" in df.columns:
        # 이름은 '누적'이지만 실제로는 30일 평균 (마스터 역검증 결과)
        at = pd.to_numeric(df["avg_temp"], errors="coerce")
        df["temp_cum_30d"] = at.rolling(30, min_periods=1).mean()

    return df


def merge_features(
    target_path,
    temp_oil_path=None,
    temp_cpi_path=None,
    temp_price_path=None,
    temp_volume_path=None,
    temp_weather_path=None,
    price_raw_path=None,
    refresh_cpi=False,
):
    print(f"마스터 로드: {target_path}")
    if not os.path.exists(target_path):
        print(f"[오류] 마스터 데이터셋 {target_path} 없음.")
        return

    df_target = pd.read_csv(target_path)
    df_target["date"] = pd.to_datetime(df_target["date"]).dt.strftime("%Y-%m-%d")
    df_target = df_target.set_index("date")

    # 1. 일별 원천 입력 병합 (반입량/기상/유가)
    path_map = {
        "oil": temp_oil_path,
        "volume": temp_volume_path,
        "weather": temp_weather_path,
    }
    for key, columns in DAILY_SOURCES.items():
        path = path_map.get(key)
        if path:
            df_target = _apply_daily_source(df_target, path, columns)

    # 1-1. 신규 도매가 날짜를 인덱스에 미리 추가
    if temp_price_path and os.path.exists(temp_price_path):
        tp = pd.read_csv(temp_price_path)
        if not tp.empty:
            tp_dates = pd.to_datetime(tp["date"]).dt.strftime("%Y-%m-%d")
            df_target = df_target.reindex(df_target.index.union(tp_dates))

    # 2. 연속 일자 인덱스 보장
    didx = pd.to_datetime(df_target.index)
    full_idx = pd.date_range(didx.min(), didx.max(), freq="D").strftime("%Y-%m-%d")
    df_target = df_target.reindex(full_idx).sort_index()

    # 3. 도매가 산출 (sidecar 기반, CPI 물가보정)
    df_target = _build_price_from_sidecar(
        df_target, temp_price_path, temp_cpi_path, price_raw_path, refresh_cpi
    )

    # 4. 휴장일 결측 채우기
    df_target = _fill_market_gaps(df_target)
    print("  결측 채움: 도매가/반입량 (보간 + ffill)")

    # 5. 파생 피처 전체 재계산
    df_target = compute_derived_features(df_target)
    print("  파생 피처 재계산: 달력/lag/이동평균/누적/타깃")

    # 6. 저장 (41컬럼, cpi 제외, 컬럼 순서 고정)
    df_target.index.name = "date"
    df_target = df_target.reindex(columns=MASTER_COLUMNS).reset_index()
    df_target.to_csv(target_path, index=False, encoding="utf-8")
    print(
        f"저장 완료: {target_path} "
        f"(총 {len(df_target)}행, {len(df_target.columns)}컬럼)"
    )


if __name__ == "__main__":
    merge_features(
        target_path=str(settings.master_dataset_path),
        temp_oil_path=str(settings.data_dir / "temp_oil.csv"),
        temp_cpi_path=str(settings.data_dir / "temp_cpi.csv"),
        temp_price_path=str(settings.data_dir / "temp_price.csv"),
        temp_volume_path=str(settings.data_dir / "temp_volume.csv"),
        temp_weather_path=str(settings.data_dir / "temp_weather.csv"),
        price_raw_path=str(settings.data_dir / "price_raw.csv"),
        refresh_cpi=True,  # __main__ 직접 실행은 전체 재환산
    )
