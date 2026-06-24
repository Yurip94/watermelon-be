"""마스터 데이터셋 CSV의 Azure Blob 다운로드/업로드.

예측 모델(app.model.predictor)이 읽는 위치와 동일하게 맞춘다:
  컨테이너 = settings.blob_container_name (기본 'data')
  blob     = settings.blob_dataset_blob  (기본 'watermelon_dataset_targets.csv')

매 실행이 빈 컨테이너인 Azure Container Apps Job 환경을 위해,
파이프라인 시작 시 download_master()로 Blob에서 마스터를 받아오고,
끝에 upload_master_dataset()로 다시 올린다(+ 날짜별 스냅샷).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from azure.storage.blob import BlobServiceClient

from app.core.config import settings


def _service_client() -> BlobServiceClient:
    if not settings.blob_storage_url or not settings.blob_storage_access_key:
        raise RuntimeError(
            "BLOB_STORAGE_URL / BLOB_STORAGE_ACCESS_KEY가 .env(환경변수)에 "
            "설정되지 않았습니다."
        )
    return BlobServiceClient(
        account_url=settings.blob_storage_url.rstrip("/"),
        credential=settings.blob_storage_access_key,
    )


def download_master(local_path: Path | None = None) -> bool:
    """Blob의 최신 마스터를 로컬로 다운로드. 성공 시 True, 없으면 False."""
    dst = local_path or settings.master_dataset_path
    client = _service_client()
    blob = client.get_blob_client(
        container=settings.blob_container_name,
        blob=settings.blob_dataset_blob,
    )
    if not blob.exists():
        print(f"[정보] Blob에 마스터 없음 ({settings.blob_dataset_blob}) — 신규 시작")
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        f.write(blob.download_blob().readall())
    print(f"다운로드 완료: {settings.blob_container_name}/{settings.blob_dataset_blob}")
    return True


def download_sidecar(local_path: Path | None = None) -> bool:
    """Blob의 원본가+CPI sidecar(price_raw.csv)를 로컬로 다운로드."""
    dst = local_path or (settings.data_dir / "price_raw.csv")
    client = _service_client()
    blob = client.get_blob_client(
        container=settings.blob_container_name,
        blob=settings.blob_sidecar_blob,
    )
    if not blob.exists():
        print(f"[정보] Blob에 sidecar 없음 ({settings.blob_sidecar_blob})")
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        f.write(blob.download_blob().readall())
    print(f"다운로드 완료: {settings.blob_container_name}/{settings.blob_sidecar_blob}")
    return True


def upload_sidecar(local_path: Path | None = None) -> None:
    """원본가+CPI sidecar를 Blob에 업로드."""
    src = local_path or (settings.data_dir / "price_raw.csv")
    if not src.exists():
        return
    client = _service_client()
    container = client.get_container_client(settings.blob_container_name)
    if not container.exists():
        container.create_container()
    container.get_blob_client(settings.blob_sidecar_blob).upload_blob(
        src.read_bytes(), overwrite=True
    )
    print(f"업로드 완료: {settings.blob_container_name}/{settings.blob_sidecar_blob}")


def upload_master_dataset(local_path: Path | None = None) -> str:
    """마스터를 Blob에 업로드. 예측이 읽는 최신본 + 날짜별 스냅샷."""
    src = local_path or settings.master_dataset_path
    if not src.exists():
        raise FileNotFoundError(f"업로드할 파일을 찾을 수 없습니다: {src}")

    today_str = date.today().isoformat()
    targets = [
        settings.blob_dataset_blob,                        # 최신본(예측이 읽음)
        f"snapshots/{today_str}/{settings.blob_dataset_blob}",  # 날짜별 보관
    ]

    client = _service_client()
    container = client.get_container_client(settings.blob_container_name)
    if not container.exists():
        container.create_container()
        print(f"컨테이너 생성: {settings.blob_container_name}")

    data = src.read_bytes()
    for blob_name in targets:
        container.get_blob_client(blob_name).upload_blob(data, overwrite=True)
        print(f"업로드 완료: {settings.blob_container_name}/{blob_name}")

    return f"{settings.blob_container_name}/{settings.blob_dataset_blob}"


if __name__ == "__main__":
    print(upload_master_dataset())
