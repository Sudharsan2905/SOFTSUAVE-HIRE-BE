from pydantic_settings import BaseSettings
from typing import List
import json


class Settings(BaseSettings):
    MONGODB_URL: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "softsuvehire"
    JWT_SECRET_KEY: str = "change-this-secret-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 360
    REFRESH_TOKEN_EXPIRE_DAYS: int = 1
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    FRONTEND_URL: str = "http://localhost:5173"
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]
    ANTHROPIC_API_KEY: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def model_post_init(self, __context):
        if isinstance(self.CORS_ORIGINS, str):
            try:
                object.__setattr__(self, "CORS_ORIGINS", json.loads(self.CORS_ORIGINS))
            except Exception:
                object.__setattr__(self, "CORS_ORIGINS", [self.CORS_ORIGINS])


settings = Settings()
