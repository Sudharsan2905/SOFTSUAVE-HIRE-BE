import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.components.scheduler import background
from app.components.storage import s3_service
from app.components.websocket.redis_bus import shutdown_bus
from app.core.config import settings
from app.core.logging import logger, setup_logging


def _validate_settings() -> None:
    """Raise RuntimeError at startup if any critical setting is missing."""
    required = [
        "JWT_SECRET_KEY",
        "MONGODB_URL",
        "DATABASE_NAME",
        "S3_BUCKET_NAME",
        "AWS_ACCESS_KEY_ID",
    ]
    missing = [k for k in required if not getattr(settings, k, "")]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")


def _warn_on_risky_cors() -> None:
    """CORS_ORIGIN_REGEX combined with credentialed requests trusts every matching origin.

    Tunnel domains (devtunnels.ms, ngrok, ...) are open to anyone, so such a regex must not
    be set in production.
    """
    if settings.CORS_ORIGIN_REGEX:
        logger.warning(
            "SECURITY: CORS_ORIGIN_REGEX={!r} is set while credentialed CORS is enabled — "
            "any origin matching it can call this API with a user's credentials. "
            "Use explicit CORS_ORIGINS in production.",
            settings.CORS_ORIGIN_REGEX,
        )
    if "*" in settings.CORS_ORIGINS:
        logger.warning("SECURITY: CORS_ORIGINS contains '*' with credentialed CORS enabled.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging(settings.LOG_LEVEL)
    _validate_settings()
    _warn_on_risky_cors()
    logger.info(f"Starting {settings.APP_NAME} API")
    client: AsyncIOMotorClient = AsyncIOMotorClient(
        settings.MONGODB_URL,
        maxPoolSize=settings.MONGO_MAX_POOL_SIZE,
        minPoolSize=settings.MONGO_MIN_POOL_SIZE,
        waitQueueTimeoutMS=settings.MONGO_WAIT_QUEUE_TIMEOUT_MS,
        serverSelectionTimeoutMS=settings.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        socketTimeoutMS=settings.MONGO_SOCKET_TIMEOUT_MS,
        maxIdleTimeMS=60_000,
        retryWrites=True,
    )
    db = client[settings.DATABASE_NAME]
    app.state.db = db
    app.state.client = client
    await _create_indexes(db)
    logger.info("Database indexes verified")

    # Reaper: APScheduler cron job on its own thread (never blocks this event loop).
    reaper = background.start(asyncio.get_running_loop()) if settings.REAPER_ENABLED else None
    yield
    background.stop(reaper)
    await s3_service.close()
    await shutdown_bus()
    client.close()
    logger.info(f"{settings.APP_NAME} API shut down")


async def _create_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index([("email", ASCENDING)], unique=True)
    await db.users.create_index([("role", ASCENDING)])
    await db.users.create_index([("phone", ASCENDING)], sparse=True)

    await db.workspaces.create_index([("created_by", ASCENDING)])
    await db.users.create_index([("workspace_ids", ASCENDING)])

    await db.question_categories.create_index([("name", ASCENDING)])

    await db.questions.create_index([("category_id", ASCENDING)])
    await db.questions.create_index([("question_type", ASCENDING)])
    await db.questions.create_index([("complexity", ASCENDING)])
    await db.questions.create_index(
        [("category_id", ASCENDING), ("question_type", ASCENDING), ("complexity", ASCENDING)]
    )

    await db.assessments.create_index([("workspace_id", ASCENDING)])
    await db.assessments.create_index([("share_link", ASCENDING)], unique=True, sparse=True)
    await db.assessments.create_index([("created_at", DESCENDING)])

    await db.assessment_submissions.create_index(
        [("assessment_id", ASCENDING), ("candidate_id", ASCENDING)], unique=True
    )
    await db.assessment_submissions.create_index([("status", ASCENDING)])
    await db.assessment_submissions.create_index([("assessment_id", ASCENDING)])
    await db.assessment_submissions.create_index(
        [("candidate_id", ASCENDING), ("assessment_id", ASCENDING)]
    )
    await db.assessment_submissions.create_index([("malpractice_count", ASCENDING)])
    await db.assessment_submissions.create_index(
        [("status", ASCENDING), ("assessment_id", ASCENDING)]
    )
    # Reaper: IN_PROGRESS sessions past their deadline
    await db.assessment_submissions.create_index(
        [("status", ASCENDING), ("current_deadline_at", ASCENDING)]
    )
    # Live-monitoring list / scoring retry
    await db.assessment_submissions.create_index(
        [("status", ASCENDING), ("started_at", DESCENDING)]
    )
    # Stale-session sweep (IN_PROGRESS with an old last_heartbeat)
    await db.assessment_submissions.create_index(
        [("status", ASCENDING), ("last_heartbeat", ASCENDING)]
    )

    await db.assessment_version_history.create_index(
        [("submission_id", ASCENDING), ("version", ASCENDING)], unique=True
    )
    await db.assessment_version_history.create_index(
        [("candidate_id", ASCENDING), ("assessment_id", ASCENDING)]
    )

    await db.assessment_shares.create_index([("assessment_id", ASCENDING)])
    await db.assessment_shares.create_index([("share_link", ASCENDING)], unique=True)

    await db.refresh_tokens.create_index([("token_hash", ASCENDING)], unique=True)
    await db.refresh_tokens.create_index([("user_id", ASCENDING)])
    await db.refresh_tokens.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)

    await db.notifications.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await db.notifications.create_index([("user_id", ASCENDING), ("is_read", ASCENDING)])
