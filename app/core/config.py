import json
from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode


def _parse_origins_value(value: Any) -> Any:
    """JSON list, CSV, or a systemd-mangled ``[a,b]`` -> list[str]."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
        if isinstance(parsed, str):
            return [parsed]
    except json.JSONDecodeError:
        pass
    return [v.strip().strip("'\"") for v in text.strip("[]").split(",") if v.strip()]


class Settings(BaseSettings):
    APP_NAME: str = ""
    APP_VERSION: str = ""
    APP_DESCRIPTION: str = ""
    LOG_LEVEL: str = "INFO"
    MAX_REACCESS_COUNT: int = 3
    MAX_MALPRACTICE_COUNT: int = 3
    HOLD_DELAY_SECONDS: int = 60
    WS_IDLE_TIMEOUT_SECONDS: int = 60
    WS_PING_SYNC_SECONDS: int = 30  # how often a heartbeat re-reads/persists session state
    # Round timing — owned by the server; the frontend has no default.
    DEFAULT_ROUND_MINUTES: int = 30
    SETUP_ALLOWANCE_MINUTES: int = 15  # time allowed to finish camera/screen setup
    SUBMIT_GRACE_SECONDS: int = 30  # network slack when accepting answers after the deadline
    # Auto-submit reaper
    # Auto-submit reaper: an APScheduler cron job on a background thread inside the API.
    # Every worker has the scheduler; a Mongo lease lets exactly one run each cycle.
    REAPER_ENABLED: bool = True
    REAPER_CRON_MINUTES: int = 10  # cron "*/N" minutes
    REAPER_MISFIRE_GRACE_SECONDS: int = 300
    REAPER_RUN_ON_STARTUP: bool = True  # one extra run shortly after boot (clears backlog)
    REAPER_STARTUP_DELAY_SECONDS: int = 30
    REAPER_LEASE_SECONDS: int = 300  # max time one run may hold the cycle lease
    # A session is overdue at deadline + grace. Runs are REAPER_CRON_MINUTES apart, so an
    # abandoned session is auto-submitted within ~ cron interval + grace of its deadline.
    REAPER_GRACE_SECONDS: int = 60
    REAPER_BATCH_SIZE: int = 200
    REAPER_SCORING_CONCURRENCY: int = 5
    USER_CACHE_TTL_SECONDS: int = 5  # 0 disables the authenticated-user cache
    # Mongo pool
    MONGO_MAX_POOL_SIZE: int = 200
    MONGO_MIN_POOL_SIZE: int = 20
    MONGO_WAIT_QUEUE_TIMEOUT_MS: int = 5000
    MONGO_SERVER_SELECTION_TIMEOUT_MS: int = 5000
    MONGO_SOCKET_TIMEOUT_MS: int = 20000
    MONGODB_URL: str = ""
    DATABASE_NAME: str = ""
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 360
    REFRESH_TOKEN_EXPIRE_DAYS: int = 1
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT_SECONDS: float = 30.0
    # Essay scoring (LLM)
    SCORING_LLM_CONCURRENCY: int = 8  # simultaneous LLM calls per process
    SCORING_LLM_MAX_ATTEMPTS: int = 4  # per essay, with exponential backoff
    SCORING_RETRY_BASE_SECONDS: float = 0.5
    SCORING_MAX_ANSWER_CHARS: int = 8000
    SCORING_MAX_ATTEMPTS: int = 3  # rescore attempts by the reaper before giving up
    SCORING_LEASE_SECONDS: int = 300  # a scoring run owns a submission this long
    # Multi-worker state (optional). Empty => single-process in-memory behaviour.
    REDIS_URL: str = ""
    REDIS_MAX_CONNECTIONS: int = 100
    WS_PRESENCE_TTL_SECONDS: int = 45  # presence key TTL, refreshed on every ping
    HOLD_SWEEP_GRACE_SECONDS: int = 30  # added to HOLD_DELAY_SECONDS for the stale sweep
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = ""
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""
    LIVEKIT_HOST: str = ""
    # NoDecode: parsed by the validator below so a .env line survives systemd's
    # EnvironmentFile quote-stripping (`["a","b"]` -> `[a,b]`) as well as plain JSON / CSV.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = []
    CORS_ORIGIN_REGEX: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> Any:
        return _parse_origins_value(value)

    def model_post_init(self, __context: Any) -> None:
        # Normalise a raw string assigned after construction (kept for compatibility).
        if isinstance(self.CORS_ORIGINS, str):
            object.__setattr__(self, "CORS_ORIGINS", _parse_origins_value(self.CORS_ORIGINS))


settings = Settings()
