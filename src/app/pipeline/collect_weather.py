"""
기상청 ASOS 일자료 수집 → data/temp_weather.csv 저장.

기상청 API 허브(apihub.kma.go.kr)의 ASOS 일자료(kma_sfcdd3.php)를 사용한다.
tm1/tm2 날짜 파라미터로 어제 하루만 증분 수집할 수 있다.

응답은 공백 구분 고정폭 텍스트이며, 헤더 라인은 '#'으로 시작한다.
필드 위치는 마스터 데이터셋(2026-06-20)과 대조하여 확정했다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

from app.core.config import settings

ASOS_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php"
SEOUL_STN = "108"

# ASOS 응답의 0-based 필드 인덱스 → 마스터 데이터셋 컬럼
# (2026-06-20 실측값과 1:1 대조하여 확정)
_FIELD_MAP = {
    0: "date",            # YYYYMMDD
    10: "avg_temp",       # TA AVG
    11: "max_temp",       # TA MAX
    13: "min_temp",       # TA MIN
    18: "humidity",       # HM AVG
    32: "sunshine_hours", # SS DAY
    35: "solar_radiation",# SI DAY
    38: "precipitation",  # RN DAY
}

# 기상청 결측/현상없음 표기값
_MISSING_TOKENS = {"-9", "-9.0", "-99", "-99.0", "-50", "-50.0"}


def _to_float(token: str, *, is_temp: bool = False, is_precip: bool = False):
    """ASOS 토큰을 float으로. 결측 표기는 NaN(강수량은 0.0)으로 변환."""
    token = token.strip()
    if token in _MISSING_TOKENS:
        # 기온은 겨울철 -9.0°C가 실제값일 수 있어 hard-missing(-50/-99)만 결측 처리
        if is_temp and token in {"-9", "-9.0"}:
            return float(token)
        # 강수량의 -9.0은 '강수없음'을 의미하므로 0.0으로 처리
        if is_precip:
            return 0.0
        return float("nan")
    try:
        return float(token)
    except ValueError:
        return float("nan")


def parse_asos_text(text: str) -> pd.DataFrame:
    """ASOS 응답 텍스트를 마스터 컬럼 DataFrame으로 파싱."""
    rows = []
    temp_cols = {"avg_temp", "max_temp", "min_temp"}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts[0].isdigit() or len(parts[0]) != 8:
            continue

        record = {}
        for idx, col in _FIELD_MAP.items():
            if idx >= len(parts):
                record[col] = float("nan")
                continue
            if col == "date":
                raw = parts[idx]
                record[col] = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
            else:
                record[col] = _to_float(
                    parts[idx],
                    is_temp=col in temp_cols,
                    is_precip=col == "precipitation",
                )
        rows.append(record)

    return pd.DataFrame(rows, columns=list(_FIELD_MAP.values()))


def fetch_weather_dataframe(
    start: date, end: date, stn: str = SEOUL_STN
) -> pd.DataFrame:
    if not settings.agri_weather_service_key:
        raise RuntimeError(
            "AGRI_WEATHER_SERVICE_KEY(KMA authKey)가 .env에 설정되지 않았습니다."
        )

    params = {
        "tm1": start.strftime("%Y%m%d"),
        "tm2": end.strftime("%Y%m%d"),
        "stn": stn,
        "authKey": settings.agri_weather_service_key,
    }
    resp = requests.get(ASOS_URL, params=params, timeout=30)
    resp.raise_for_status()

    df = parse_asos_text(resp.text)
    return df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def resolve_incremental_start(master_path: str, backfill_start: str) -> str:
    """마스터의 마지막 기온 관측일 다음 날을 YYYYMMDD로 반환 (없으면 backfill_start)."""
    try:
        df = pd.read_csv(master_path, usecols=["date", "avg_temp"])
    except (FileNotFoundError, ValueError):
        return backfill_start

    valid = df.dropna(subset=["avg_temp"])
    if valid.empty:
        return backfill_start

    last_date = pd.to_datetime(valid["date"]).max()
    return (last_date + timedelta(days=1)).strftime("%Y%m%d")


if __name__ == "__main__":
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    yesterday = date.today() - timedelta(days=1)
    start_str = resolve_incremental_start(str(settings.master_dataset_path), "20200101")
    start = date(int(start_str[:4]), int(start_str[4:6]), int(start_str[6:8]))

    if start > yesterday:
        print("기상 데이터가 이미 최신입니다. 수집 생략.")
    else:
        print(
            f"ASOS 기상 수집(서울 {SEOUL_STN}): "
            f"{start.isoformat()} ~ {yesterday.isoformat()}"
        )
        df = fetch_weather_dataframe(start, yesterday)
        output_path = settings.data_dir / "temp_weather.csv"
        df.to_csv(str(output_path), index=False, encoding="utf-8")
        print(f"저장 완료: {output_path} (총 {len(df)}행)")
