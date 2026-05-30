from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.core.config import settings
from app.core.logging import logger, setup_logging


def _validate_settings() -> None:
    """Raise RuntimeError at startup if any critical setting is missing."""
    required = ["JWT_SECRET_KEY", "MONGODB_URL", "DATABASE_NAME"]
    missing = [k for k in required if not getattr(settings, k, "")]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging(settings.LOG_LEVEL)
    _validate_settings()
    logger.info(f"Starting {settings.APP_NAME} API")
    client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]
    app.state.db = db
    app.state.client = client
    Path(settings.SCREENSHOTS_DIR).mkdir(parents=True, exist_ok=True)
    logger.info(f"Screenshots directory ready: {settings.SCREENSHOTS_DIR}")
    await _create_indexes(db)
    logger.info("Database indexes verified")
    yield
    client.close()
    logger.info(f"{settings.APP_NAME} API shut down")


async def _create_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index([("email", ASCENDING)], unique=True)
    await db.users.create_index([("role", ASCENDING)])

    await db.candidates.create_index([("user_id", ASCENDING)], unique=True)
    await db.candidates.create_index([("phone", ASCENDING)])

    await db.workspaces.create_index([("created_by", ASCENDING)])
    await db.workspaces.create_index([("members.user_id", ASCENDING)])

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

    await db.refresh_tokens.create_index([("token_hash", ASCENDING)], unique=True)
    await db.refresh_tokens.create_index([("user_id", ASCENDING)])
    await db.refresh_tokens.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)

    # Notifications: per-user lookup + unread filter + time ordering
    await db.notifications.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await db.notifications.create_index([("user_id", ASCENDING), ("is_read", ASCENDING)])
