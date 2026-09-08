from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # requirements.txt only installs psycopg (v3), not psycopg2 —
        # a bare "postgresql://" URL would make SQLAlchemy default to psycopg2.
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value


settings = Settings()
