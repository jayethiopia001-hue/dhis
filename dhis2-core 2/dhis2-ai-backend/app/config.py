"""
Central settings, loaded from environment / .env.

Drop real credentials into .env (gitignored) — never into this file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Postgres (GCP-hosted)
    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: str

    # Cloud SQL instance connection name (project:region:instance).
    # When set, the Cloud SQL Python Connector is used instead of direct TCP.
    cloud_sql_instance: str = ""

    # DHIS2 — for future direct FastAPI -> DHIS2 calls (SPEC.md §5.1/§5.2).
    # Not required for step 1.
    dhis2_base_url: str = "http://localhost:8080"
    dhis2_username: str = ""
    dhis2_password: str = ""

    # Anthropic — for the future GenAI layer (SPEC.md §5.3). Not required
    # for step 1.
    anthropic_api_key: str = ""

    # Gemini — used for Situational Analysis and Evidence Synthesis
    gemini_api_key: str = ""

    app_env: str = "local"

    @property
    def database_url(self) -> str:
        if self.cloud_sql_instance:
            # pg8000 driver used with the Cloud SQL Python Connector
            return (
                f"postgresql+pg8000://{self.postgres_user}:{self.postgres_password}"
                f"@/{self.postgres_db}"
            )
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def db_connect_args(self) -> dict:
        return {"sslmode": "disable"}


settings = Settings()
