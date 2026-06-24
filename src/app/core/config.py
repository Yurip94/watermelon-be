from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 레포 루트: src/app/core/config.py -> parents[3] == repo root (컨테이너에선 /app)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "watermelon-backend"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/watermelon"
    agri_weather_service_key: str = ""
    agri_weather_search_year: int = Field(default_factory=lambda: date.today().year)
    agri_weather_obsr_spot_cd: str = "137180A001"
    kamis_cert_key: str = ""
    kamis_cert_id: str = ""

    # Blob에 올라가는 학습/추론 입력 CSV
    blob_storage_url: str = ""
    blob_storage_access_key: str = ""
    blob_container_name: str = "data"
    blob_dataset_blob: str = "watermelon_dataset_targets.csv"
    # 원본가+CPI sidecar (마스터 밖에서 관리)
    blob_sidecar_blob: str = "price_raw.csv"
    # /price 호출 감사 로그(JSONL, 일별 회전) prefix — 컨테이너는 위와 동일
    blob_audit_prefix: str = "audit/prices"

    # 이미지에 동봉되는 모델 아티팩트 경로
    model_artifact_path: str = "/app/src/app/model/ridge_production.joblib"

    # 매일 1회 예측 실행 시각(KST)
    prediction_cron_hour: int = 3
    prediction_cron_minute: int = 0
    prediction_timezone: str = "Asia/Seoul"

    # === 데이터 수집 파이프라인 (도매가/반입량/유가/CPI/기상) ===
    kosis_api_key: str = ""
    kosis_user_id: str = ""
    opinet_api_key: str = ""
    opinet_backfill_start: str = "20200101"
    # 가락시장 공공데이터: 반입량(data22) / 단위별 도매가(data36)
    garak_api_id: str = ""
    garak_api_passwd: str = ""
    garak_price_id: str = ""
    garak_price_passwd: str = ""
    # 도매가 물가보정 기준 CPI. 0이면 수집된 CPI 중 최신 발표월 자동 사용.
    cpi_base: float = 0.0
    # 마스터/중간산출물 디렉터리 (컨테이너 ephemeral, Blob에서 받아옴)
    data_dir: Path = PROJECT_ROOT / "data"
    # 데이터 수집 스케줄(로컬 scheduler.py용; 예측 3시 전에 끝나도록 2시 기본)
    scheduler_timezone: str = "Asia/Seoul"
    scheduler_hour: int = 2
    scheduler_minute: int = 0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


    @property
    def master_dataset_path(self) -> Path:
        return self.data_dir / "watermelon_dataset_targets.csv"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
