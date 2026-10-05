from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://sebi_user:sebi_dev_2026@localhost:5432/sebi_sentinel"
    artifacts_dir: str = "artifacts"
    reports_dir: str = "reports"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
