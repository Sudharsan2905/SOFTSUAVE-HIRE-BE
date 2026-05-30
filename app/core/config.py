import json
from typing import Any

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = ""
    APP_VERSION: str = ""
    APP_DESCRIPTION: str = ""
    MONGODB_URL: str = ""
    DATABASE_NAME: str = ""
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 360
    REFRESH_TOKEN_EXPIRE_DAYS: int = 1
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    CORS_ORIGINS: list[str] = []
    OPENAI_API_KEY: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    LOG_LEVEL: str = "INFO"
    MAX_REACCESS_COUNT: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def model_post_init(self, __context: Any) -> None:
        if isinstance(self.CORS_ORIGINS, str):
            try:
                object.__setattr__(self, "CORS_ORIGINS", json.loads(self.CORS_ORIGINS))
            except Exception:
                object.__setattr__(self, "CORS_ORIGINS", [self.CORS_ORIGINS])


settings = Settings()
