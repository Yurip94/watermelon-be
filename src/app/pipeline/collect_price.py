"""
가락시장 수박 도매가(원/kg) 수집 → data/temp_price.csv 저장.

서울시농수산식품공사 가락시장 공공데이터(garak.co.kr)의 단위별 도매가격
(dataid=data36)을 사용한다. 수박(일반) '상' 등급의 모든 거래단위 평균가를
1kg 기준으로 환산하여 평균낸 값이 마스터 데이터셋의 wholesale_price와 일치함을
최근 데이터로 검증했다 (2026-06 구간 오차 0.0).

산출 규칙:
  - PUM_NM == '수박(일반)', G_NAME == '상'
  - 각 행을 원/kg으로 환산: 단위가 '1 kg'이면 AV_P 그대로,
    'N kg개'이면 AV_P / N
  - 환산값들의 단순 평균 = wholesale_price

date 파라미터로 하루씩 조회하므로 어제 하루만 증분 수집할 수 있다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import requests

from app.core.config import settings

GARAK_URL = "http://www.garak.co.kr/homepage/publicdata/dataJsonOpen.do"
PRICE_DATAID = "data36"
WATERMELON_NAME = "수박(일반)"
TARGET_GRADE = "상"


def _to_num(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _unit_kg(unit_name) -> float | None:
    """'1 kg' -> 1.0, '8 kg개' -> 8.0."""
    m = re.search(r"([\d.]+)\s*kg", str(unit_name))
    return float(m.group(1)) if m else None


def _prev_business_day(d: date) -> date:
    """전 영업일(일요일 제외)."""
    p = d - timedelta(days=1)
    if p.weekday() == 6:  # 일요일
        p -= timedelta(days=1)
    return p


def fetch_price(target: date) -> float | None:
    """해당 날짜의 수박(일반) '상' 등급 평균 도매가(원/kg). 데이터 없으면 None."""
    if not settings.garak_price_id or not settings.garak_price_passwd:
        raise RuntimeError(
            "GARAK_PRICE_ID / GARAK_PRICE_PASSWD가 .env에 설정되지 않았습니다."
        )

    params = {
        "id": settings.garak_price_id,
        "passwd": settings.garak_price_passwd,
        "dataid": PRICE_DATAID,
        "pagesize": "200",
        "pageidx": "1",
        "portal.templet": "false",
        "s_date": target.strftime("%Y%m%d"),
        "s_date_p": _prev_business_day(target).strftime("%Y%m%d"),
        "s_date_p7": (target - timedelta(days=7)).strftime("%Y%m%d"),
        "p_pos_gubun": "1",   # 가락시장
        "s_pum_nm": "2",      # 청과
        "s_pummok": "수박",
    }
    resp = requests.get(GARAK_URL, params=params, timeout=30)
    resp.raise_for_status()

    # 휴장일(일요일/공휴일)은 빈 응답(비 JSON)이 올 수 있음 → 데이터 없음으로 처리
    try:
        rows = resp.json().get("resultData") or []
    except ValueError:
        return None
    per_kg = []
    for row in rows:
        if row.get("PUM_NM") != WATERMELON_NAME or row.get("G_NAME") != TARGET_GRADE:
            continue
        kg = _unit_kg(row.get("U_NAME"))
        avg = _to_num(row.get("AV_P"))
        if kg is None or avg is None:
            continue
        per_kg.append(avg if kg == 1 else avg / kg)

    if not per_kg:
        return None
    return round(sum(per_kg) / len(per_kg), 1)


def fetch_price_dataframe(start: date, end: date) -> pd.DataFrame:
    records = []
    current = start
    while current <= end:
        price = fetch_price(current)
        if price is not None:
            records.append({"date": current.isoformat(), "wholesale_price": price})
        current += timedelta(days=1)
    return pd.DataFrame(records, columns=["date", "wholesale_price"])


def resolve_incremental_start(master_path: str, backfill_start: str) -> str:
    """마스터의 마지막 도매가일 다음 날을 YYYYMMDD로 반환 (없으면 backfill)."""
    try:
        df = pd.read_csv(master_path, usecols=["date", "wholesale_price"])
    except (FileNotFoundError, ValueError):
        return backfill_start

    valid = df.dropna(subset=["wholesale_price"])
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
        print("도매가 데이터가 이미 최신입니다. 수집 생략.")
    else:
        print(f"가락 수박 도매가 수집: {start.isoformat()} ~ {yesterday.isoformat()}")
        df = fetch_price_dataframe(start, yesterday)
        output_path = settings.data_dir / "temp_price.csv"
        df.to_csv(str(output_path), index=False, encoding="utf-8")
        print(f"저장 완료: {output_path} (총 {len(df)}행)")
