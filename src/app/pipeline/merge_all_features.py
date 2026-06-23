"""
수집된 원천 데이터(temp_*.csv)를 마스터 데이터셋에 병합.

원천 입력 5종을 날짜 기준으로 마스터에 갱신/추가한다:
  - 도매가  : wholesale_price            (temp_price.csv, raw 원/kg)
  - 반입량  : trade_volume               (temp_volume.csv)
  - 기상    : avg/max/min_temp, humidity, sunshine_hours, solar_radiation, precipitation
            (temp_weather.csv)
  - 유가    : oil_gasoline, oil_diesel    (temp_oil.csv)
  - CPI     : cpi (월별 → 일별 forward-fill, temp_cpi.csv)

도매가(wholesale_price)는 CPI 물가보정된 실질가격이다.
  wholesale_price = raw(원/kg) × (기준CPI / 그달CPI)
  - 기준CPI = 수집된 CPI 중 최신 발표월 값 (매달 갱신)
  - raw 원본가는 sidecar(price_raw.csv)에 보관하고, 매 실행마다 전체를 최신 CPI로
    다시 계산한다. → 새 CPI 발표 시 전 구간이 일관된 실질가격으로 재환산됨.

또한 유가 7일 이동평균(oil_ma_7d)을 재계산한다.

※ 주의: lag / 이동평균(price_ma_*) / 누적기상(sunshine_cum_*) / y_t1~y_t7 / 달력 등
  나머지 파생 피처 재계산은 아직 미구현. (다음 작업)
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


def _recompute_wholesale_price(df_target, temp_price_path, price_raw_path, cpi_base):
    """raw 원본가 sidecar 기반으로 전체 도매가를 최신 CPI로 재환산.

    wholesale_price = raw × (cpi_base / 그날 cpi)
    raw 원본은 price_raw_path(sidecar)에 누적 보관. sidecar가 없으면 기존 마스터에서
    역산하여 1회 부트스트랩(raw = wholesale × cpi / cpi_base).
    """
    if cpi_base is None:
        print("  [경고] 기준 CPI 없음 — 도매가 재환산 건너뜀")
        return df_target

    # 1. raw 원본가 맵 구성
    if price_raw_path and os.path.exists(price_raw_path):
        raw_df = pd.read_csv(price_raw_path)
        raw_df["date"] = pd.to_datetime(raw_df["date"]).dt.strftime("%Y-%m-%d")
        raw_map = dict(zip(raw_df["date"], pd.to_numeric(raw_df["raw"])))
    else:
        # 최초 1회: 기존 마스터 도매가를 raw로 역산 (round-trip 보존)
        w = pd.to_numeric(df_target.get("wholesale_price"), errors="coerce")
        c = pd.to_numeric(df_target.get("cpi"), errors="coerce")
        recon = (w * c / cpi_base).dropna()
        raw_map = recon.round(4).to_dict()
        print(f"  raw 부트스트랩: 기존 마스터에서 {len(raw_map)}일 역산")

    # 2. 신규 raw 추가 (temp_price = nominal raw 원/kg)
    if temp_price_path and os.path.exists(temp_price_path):
        tp = pd.read_csv(temp_price_path)
        if not tp.empty:
            tp["date"] = pd.to_datetime(tp["date"]).dt.strftime("%Y-%m-%d")
            for _, r in tp.iterrows():
                raw_map[r["date"]] = round(float(r["wholesale_price"]), 4)

    if not raw_map:
        return df_target

    # 3. sidecar 저장 (raw 원본 = 단일 진실)
    if price_raw_path:
        out = pd.DataFrame(
            {"date": list(raw_map.keys()), "raw": list(raw_map.values())}
        ).sort_values("date")
        out.to_csv(price_raw_path, index=False, encoding="utf-8")

    # 4. 전체 재환산: wholesale_price = raw × (cpi_base / cpi)
    raw_series = pd.Series(raw_map)
    df_target = df_target.reindex(df_target.index.union(raw_series.index))
    common = df_target.index.intersection(raw_series.index)
    cpi = pd.to_numeric(df_target.loc[common, "cpi"], errors="coerce")
    df_target.loc[common, "wholesale_price"] = (
        raw_series.loc[common] * cpi_base / cpi
    ).round(1)
    print(f"  도매가 전체 재환산: {len(common)}일 (기준 CPI={cpi_base})")
    return df_target


def _fill_market_gaps(df_target):
    """휴장일(일/공휴일) 도매가·반입량 결측 채우기.

    영업일 사이의 닫힌 구간은 선형 보간, 미래 영업일이 아직 없는 마지막 구간은
    직전 영업일 값으로 임시 ffill (다음 영업일 들어오면 보간으로 교정됨).
    """
    for col in ["wholesale_price", "trade_volume"]:
        if col not in df_target.columns:
            continue
        s = pd.to_numeric(df_target[col], errors="coerce")
        s = s.interpolate(method="linear", limit_area="inside")  # 닫힌 구간 보간
        s = s.ffill()  # 마지막 열린 구간 임시 ffill
        df_target[col] = s.round(1)
    return df_target


def compute_derived_features(df):
    """검증된 공식으로 파생 피처 전체 재계산 (마스터 역검증 100% 일치 기준).

    이동평균 당일 포함/제외가 컬럼마다 다른 것은 원본 데이터 그대로 재현한 것:
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

    # 1-1. 신규 도매가 날짜를 인덱스에 미리 추가 (CPI 매핑 대상 포함되도록)
    if temp_price_path and os.path.exists(temp_price_path):
        tp = pd.read_csv(temp_price_path)
        if not tp.empty:
            tp_dates = pd.to_datetime(tp["date"]).dt.strftime("%Y-%m-%d")
            df_target = df_target.reindex(df_target.index.union(tp_dates))

    # 2. 연속 일자 인덱스 보장 (롤링/보간이 달력일 기준으로 동작하도록)
    didx = pd.to_datetime(df_target.index)
    full_idx = pd.date_range(didx.min(), didx.max(), freq="D").strftime("%Y-%m-%d")
    df_target = df_target.reindex(full_idx).sort_index()

    # 3. CPI (월별 → 일별 매핑 + forward-fill) + 기준 CPI 산출
    cpi_base = None
    if temp_cpi_path and os.path.exists(temp_cpi_path):
        df_cpi = pd.read_csv(temp_cpi_path)
        cpi_dict = dict(zip(df_cpi["date"], df_cpi["cpi"]))

        # 기준 CPI: 수동 고정값(settings.cpi_base>0) 우선, 아니면 최신 발표월 자동
        if settings.cpi_base and settings.cpi_base > 0:
            cpi_base = settings.cpi_base
        elif cpi_dict:
            cpi_base = cpi_dict[max(cpi_dict)]

        if "cpi" in df_target.columns:
            existing_cpi = df_target["cpi"]
        else:
            existing_cpi = pd.Series(index=df_target.index, dtype="float64")

        def map_cpi(d):
            month_key = d[:7]
            if month_key in cpi_dict:
                return cpi_dict[month_key]
            prev = existing_cpi.get(d)
            return prev if pd.notna(prev) else None

        df_target["cpi"] = df_target.index.map(map_cpi)
        df_target["cpi"] = df_target["cpi"].ffill()
        print("  병합: temp_cpi.csv → cpi (월별 ffill)")
    elif temp_cpi_path:
        print(f"  [경고] {temp_cpi_path} 없음 — CPI 건너뜀")

    # 4. 도매가 전체 재환산 (raw sidecar 기반, 최신 CPI 기준)
    if temp_price_path or price_raw_path:
        df_target = _recompute_wholesale_price(
            df_target, temp_price_path, price_raw_path, cpi_base
        )
        df_target = df_target.sort_index()

    # 5. 휴장일 결측 채우기 (도매가/반입량: 보간 + 마지막 구간 ffill)
    df_target = _fill_market_gaps(df_target)
    print("  결측 채움: 도매가/반입량 (보간 + ffill)")

    # 6. 파생 피처 전체 재계산
    df_target = compute_derived_features(df_target)
    print("  파생 피처 재계산: 달력/lag/이동평균/누적/타깃")

    # 7. 저장
    df_target.index.name = "date"
    df_target = df_target.reset_index()
    df_target.to_csv(target_path, index=False, encoding="utf-8")
    print(f"저장 완료: {target_path} (총 {len(df_target)}행)")


if __name__ == "__main__":
    merge_features(
        target_path=str(settings.master_dataset_path),
        temp_oil_path=str(settings.data_dir / "temp_oil.csv"),
        temp_cpi_path=str(settings.data_dir / "temp_cpi.csv"),
        temp_price_path=str(settings.data_dir / "temp_price.csv"),
        temp_volume_path=str(settings.data_dir / "temp_volume.csv"),
        temp_weather_path=str(settings.data_dir / "temp_weather.csv"),
        price_raw_path=str(settings.data_dir / "price_raw.csv"),
    )
