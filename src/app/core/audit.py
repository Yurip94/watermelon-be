"""/price 호출 감사(audit) 로그.

KST 기준 일별로 회전되는 Append Blob(JSONL)에 한 줄씩 누적.
감사 로깅 실패는 사용자 요청에 영향을 주지 않는다(예외를 삼키고 서버 로그에만 기록).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, BlobType
from fastapi import Request

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_client_ip(request: Request) -> str:
    """프록시 뒤(ACA)에서도 올바른 클라이언트 IP를 얻는다."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


class PriceAuditLogger:
    def __init__(self) -> None:
        self._service: BlobServiceClient | None = None
        self._initialized_blobs: set[str] = set()
        self._lock = Lock()

    def _service_client(self) -> BlobServiceClient:
        if self._service is None:
            if not settings.blob_storage_url or not settings.blob_storage_access_key:
                raise RuntimeError(
                    "BLOB_STORAGE_URL / BLOB_STORAGE_ACCESS_KEY 미설정"
                )
            self._service = BlobServiceClient(
                account_url=settings.blob_storage_url.rstrip("/"),
                credential=settings.blob_storage_access_key,
            )
        return self._service

    def _blob_name_for(self, ts: datetime) -> str:
        return f"{settings.blob_audit_prefix}/{ts.date().isoformat()}.jsonl"

    def _ensure_append_blob(self, blob_name: str):
        client = self._service_client().get_blob_client(
            container=settings.blob_container_name,
            blob=blob_name,
        )
        if blob_name not in self._initialized_blobs:
            with self._lock:
                if blob_name not in self._initialized_blobs:
                    try:
                        client.create_append_blob()
                    except ResourceExistsError:
                        pass
                    self._initialized_blobs.add(blob_name)
        return client

    def log_event(self, event: dict) -> None:
        try:
            ts = datetime.now(tz=ZoneInfo(settings.prediction_timezone))
            event = {"ts": ts.isoformat(), **event}
            blob_name = self._blob_name_for(ts)
            client = self._ensure_append_blob(blob_name)
            line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
            client.append_block(line.encode("utf-8"))
        except Exception:
            logger.exception("audit log failed: %s", event)


audit_logger = PriceAuditLogger()
