"""
월간 CPI 갱신 + 전체 물가보정 재환산 스크립트.

매달 새 CPI가 발표되면 1회 실행:
  0. Blob에서 마스터 + sidecar 다운로드
  1. KOSIS에서 최신 CPI 수집 (temp_cpi.csv)
  2. sidecar의 cpi를 새 값으로 재매핑 → 기준CPI(최신월) 갱신
     → 전체 wholesale_price 재환산 (raw_price × 최신CPI/그달CPI)
  3. 파생 피처 전체 재계산
  4. Blob 업로드 (마스터 + sidecar)

※ 이 작업은 마스터 전체(타깃 포함)를 바꾸므로 이후 모델 재학습이 필요하다.
"""

from __future__ import annotations

import sys
import traceback
from datetime import date

from app.core.config import settings


def _blob_creds() -> bool:
    return bool(settings.blob_storage_url and settings.blob_storage_access_key)


def main() -> int:
    print("=== 월간 CPI 갱신 + 전체 재환산 시작 ===")
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 0. Blob에서 마스터 + sidecar 다운로드
        if _blob_creds():
            from app.pipeline.upload_to_blob import download_master, download_sidecar

            download_master()
            download_sidecar()

        # 1. KOSIS 최신 CPI 수집
        from app.pipeline.collect_cpi import fetch_cpi_data, save_to_csv

        current_month = date.today().strftime("%Y%m")
        records = fetch_cpi_data("202001", current_month)
        temp_cpi = str(settings.data_dir / "temp_cpi.csv")
        save_to_csv(records, temp_cpi)

        # 2~3. CPI 재매핑 + 전체 재환산 + 파생 재계산
        from app.pipeline.merge_all_features import merge_features

        merge_features(
            target_path=str(settings.master_dataset_path),
            temp_cpi_path=temp_cpi,
            price_raw_path=str(settings.data_dir / "price_raw.csv"),
            refresh_cpi=True,   # 핵심: CPI 재매핑 + 전체 wholesale 재환산
        )

        # 4. Blob 업로드 (마스터 + sidecar)
        if _blob_creds():
            from app.pipeline.upload_to_blob import (
                upload_master_dataset,
                upload_sidecar,
            )

            print(f"업로드: {upload_master_dataset()}")
            upload_sidecar()
        else:
            print("[스킵] Blob 인증 정보 미설정 — 업로드 생략.")

        print("=== 완료 (모델 재학습 필요) ===")
        return 0
    except Exception:
        print("[오류] 월간 CPI 갱신 실패:")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
