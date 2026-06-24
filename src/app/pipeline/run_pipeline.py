"""
매일 데이터 파이프라인 실행기 (CPI 미갱신).

실행 순서:
  0. Blob에서 마스터 + sidecar 다운로드 (컨테이너 ephemeral 대비)
  1. 오피넷 유가 수집 (증분)
  2. 가락 수박 도매가 수집 (data36, 증분)
  3. 가락 수박 반입량 수집 (data22, 증분)
  4. 기상청 ASOS 날씨 수집 (증분)
  5. 피처 병합 → 마스터 갱신 (sidecar 기존 CPI 사용, 과거 불변)
  6. Blob 업로드 (마스터 + sidecar)

※ CPI 갱신 + 전체 물가보정 재환산은 월간 스크립트 update_cpi.py에서 수행.
"""

from __future__ import annotations

import sys
import traceback
from datetime import date, timedelta

from app.core.config import settings


def _blob_creds() -> bool:
    return bool(settings.blob_storage_url and settings.blob_storage_access_key)


def _step(name: str) -> None:
    print(f"\n{'=' * 50}")
    print(f"  {name}")
    print(f"{'=' * 50}")


def run_download_master() -> bool:
    _step("0/6  Blob에서 마스터 + sidecar 다운로드")
    try:
        if not _blob_creds():
            print("[스킵] Blob 인증 정보 미설정 — 로컬 파일 사용.")
            return True
        from app.pipeline.upload_to_blob import download_master, download_sidecar

        download_master()
        download_sidecar()
        return True
    except Exception:
        print("[오류] 다운로드 실패:")
        traceback.print_exc()
        return False


def run_collect_oil() -> bool:
    _step("1/7  유가 수집 (오피넷, 증분)")
    try:
        from app.pipeline.collect_opinet import (
            fetch_oil_prices,
            resolve_incremental_start,
            save_to_csv,
        )

        yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        start_str = resolve_incremental_start(
            str(settings.master_dataset_path), settings.opinet_backfill_start
        )

        if start_str > yesterday_str:
            print("유가 데이터 이미 최신. 스킵.")
            return True

        data = fetch_oil_prices(start_str, yesterday_str)
        save_to_csv(data, str(settings.data_dir / "temp_oil.csv"))
        return True
    except Exception:
        print("[오류] 유가 수집 실패:")
        traceback.print_exc()
        return False


def run_collect_price() -> bool:
    _step("2/6  수박 도매가 수집 (가락 data36, 증분)")
    try:
        from app.pipeline.collect_price import (
            fetch_price_dataframe,
            resolve_incremental_start,
        )

        yesterday = date.today() - timedelta(days=1)
        start_str = resolve_incremental_start(
            str(settings.master_dataset_path), "20200101"
        )
        start = date(int(start_str[:4]), int(start_str[4:6]), int(start_str[6:8]))

        if start > yesterday:
            print("도매가 데이터 이미 최신. 스킵.")
            return True

        df = fetch_price_dataframe(start, yesterday)
        output_path = settings.data_dir / "temp_price.csv"
        df.to_csv(str(output_path), index=False, encoding="utf-8")
        print(f"저장 완료: {output_path} (총 {len(df)}행)")
        return True
    except Exception:
        print("[오류] 도매가 수집 실패:")
        traceback.print_exc()
        return False


def run_collect_volume() -> bool:
    _step("3/6  수박 반입량 수집 (가락 data22, 증분)")
    try:
        from app.pipeline.collect_volume import (
            fetch_volume_dataframe,
            resolve_incremental_start,
        )

        yesterday = date.today() - timedelta(days=1)
        start_str = resolve_incremental_start(
            str(settings.master_dataset_path), "20200101"
        )
        start = date(int(start_str[:4]), int(start_str[4:6]), int(start_str[6:8]))

        if start > yesterday:
            print("반입량 데이터 이미 최신. 스킵.")
            return True

        df = fetch_volume_dataframe(start, yesterday)
        output_path = settings.data_dir / "temp_volume.csv"
        df.to_csv(str(output_path), index=False, encoding="utf-8")
        print(f"저장 완료: {output_path} (총 {len(df)}행)")
        return True
    except Exception:
        print("[오류] 반입량 수집 실패:")
        traceback.print_exc()
        return False


def run_collect_weather() -> bool:
    _step("4/6  기상 수집 (KMA ASOS, 증분)")
    try:
        from app.pipeline.collect_weather import (
            fetch_weather_dataframe,
            resolve_incremental_start,
        )

        yesterday = date.today() - timedelta(days=1)
        start_str = resolve_incremental_start(
            str(settings.master_dataset_path), "20200101"
        )
        start = date(int(start_str[:4]), int(start_str[4:6]), int(start_str[6:8]))

        if start > yesterday:
            print("기상 데이터 이미 최신. 스킵.")
            return True

        df = fetch_weather_dataframe(start, yesterday)
        output_path = settings.data_dir / "temp_weather.csv"
        df.to_csv(str(output_path), index=False, encoding="utf-8")
        print(f"저장 완료: {output_path} (총 {len(df)}행)")
        return True
    except Exception:
        print("[오류] 기상 수집 실패:")
        traceback.print_exc()
        return False


def run_merge() -> bool:
    _step("5/6  피처 병합 → 마스터 데이터셋 갱신 (CPI 미갱신)")
    try:
        from app.pipeline.merge_all_features import merge_features

        # 매일은 CPI를 갱신하지 않음(refresh_cpi=False) → sidecar의 기존 cpi 사용,
        # 과거 wholesale 불변. CPI 재환산은 월간 update_cpi에서.
        merge_features(
            target_path=str(settings.master_dataset_path),
            temp_oil_path=str(settings.data_dir / "temp_oil.csv"),
            temp_cpi_path=None,
            temp_price_path=str(settings.data_dir / "temp_price.csv"),
            temp_volume_path=str(settings.data_dir / "temp_volume.csv"),
            temp_weather_path=str(settings.data_dir / "temp_weather.csv"),
            price_raw_path=str(settings.data_dir / "price_raw.csv"),
            refresh_cpi=False,
        )
        return True
    except Exception:
        print("[오류] 피처 병합 실패:")
        traceback.print_exc()
        return False


def run_upload() -> bool:
    _step("6/6  Azure Blob 업로드 (마스터 + sidecar)")
    try:
        if not _blob_creds():
            print("[스킵] Blob 인증 정보 미설정. 업로드를 건너뜁니다.")
            return True

        from app.pipeline.upload_to_blob import (
            upload_master_dataset,
            upload_sidecar,
        )

        blob_path = upload_master_dataset()
        upload_sidecar()
        print(f"업로드 성공: {blob_path}")
        return True
    except Exception:
        print("[오류] Blob 업로드 실패:")
        traceback.print_exc()
        return False


def main() -> int:
    print("수박 가격 예측 데이터 파이프라인 시작")
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    steps = [
        run_download_master,
        run_collect_oil,
        run_collect_price,
        run_collect_volume,
        run_collect_weather,
        run_merge,
        run_upload,
    ]

    failed = []
    for step_fn in steps:
        ok = step_fn()
        if not ok:
            failed.append(step_fn.__name__)

    print(f"\n{'=' * 50}")
    if failed:
        print(f"[완료] 일부 스텝 실패: {', '.join(failed)}")
        return 1
    else:
        print("[완료] 전체 파이프라인 성공")
        return 0


if __name__ == "__main__":
    sys.exit(main())
