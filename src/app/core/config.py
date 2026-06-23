from datetime import date
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # 이미지에 동봉되는 모델 아티팩트 경로
    model_artifact_path: str = "/app/src/app/model/ridge_production.joblib"

    # 매일 1회 예측 실행 시각(KST)
    prediction_cron_hour: int = 3
    prediction_cron_minute: int = 0
    prediction_timezone: str = "Asia/Seoul"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
