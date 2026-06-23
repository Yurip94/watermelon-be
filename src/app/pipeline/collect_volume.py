"""
가락시장 수박 반입량 수집 → data/temp_volume.csv 저장.

서울시농수산식품공사 가락시장 공공데이터(garak.co.kr)의 일별 품목별 반입량
(dataid=data22)을 사용한다. 수박(PUM_CD=22100)의 SUM_TOT(전 법인 합계, 단위 톤)이
마스터 데이터셋의 trade_volume과 일치함을 과거 데이터로 검증했다.
(예: 2023-07-31 SUM_TOT=570.934 → 마스터 trade_volume=571.0)

date 파라미터(YYYYMMDD)로 하루씩 조회하므로 어제 하루만 증분 수집할 수 있다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

from app.core.config import settings

GARAK_URL = "http://www.garak.co.kr/homepage/publicdata/dataJsonOpen.do"
WATERMELON_PUM_CD = "22100"
VOLUME_DATAID = "data22"


def fetch_volume(target: date) -> float | None:
    """해당 날짜의 수박 총 반입량(톤, 정수 반올림)을 반환. 데이터 없으면 None."""
    if not settings.garak_api_id or not settings.garak_api_passwd:
        raise RuntimeError(
            "GARAK_API_ID / GARAK_API_PASSWD가 .env에 설정되지 않았습니다."
        )

    params = {
        "id": settings.garak_api_id,
        "passwd": settings.garak_api_passwd,
        "dataid": VOLUME_DATAID,
        "pagesize": "500",
        "pageidx": "1",
        "portal.templet": "false",
        "date": target.strftime("%Y%m%d"),
    }
    resp = requests.get(GARAK_URL, params=params, timeout=30)
    resp.raise_for_status()

    # 휴장일(일요일/공휴일)은 빈 응답(비 JSON)이 올 수 있음 → 데이터 없음으로 처리
    try:
        rows = resp.json().get("resultData", [])
    except ValueError:
        return None
    for row in rows:
        if str(row.get("PUM_CD")) == WATERMELON_PUM_CD:
            total = row.get("SUM_TOT")
            if total is None:
                return None
            return float(round(float(total)))
    return None


def fetch_volume_dataframe(start: date, end: date) -> pd.DataFrame:
    records = []
    current = start
    while current <= end:
        volume = fetch_volume(current)
        if volume is not None:
            records.append({"date": current.isoformat(), "trade_volume": volume})
        current += timedelta(days=1)

    return pd.DataFrame(records, columns=["date", "trade_volume"])


def resolve_incremental_start(master_path: str, backfill_start: str) -> str:
    """마스터의 마지막 반입량 관측일 다음 날을 YYYYMMDD로 반환 (없으면 backfill)."""
    try:
        df = pd.read_csv(master_path, usecols=["date", "trade_volume"])
    except (FileNotFoundError, ValueError):
        return backfill_start

    valid = df.dropna(subset=["trade_volume"])
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
        print("반입량 데이터가 이미 최신입니다. 수집 생략.")
    else:
        print(f"가락 수박 반입량 수집: {start.isoformat()} ~ {yesterday.isoformat()}")
        df = fetch_volume_dataframe(start, yesterday)
        output_path = settings.data_dir / "temp_volume.csv"
        df.to_csv(str(output_path), index=False, encoding="utf-8")
        print(f"저장 완료: {output_path} (총 {len(df)}행)")
