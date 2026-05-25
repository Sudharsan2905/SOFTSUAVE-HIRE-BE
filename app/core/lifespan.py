from contextlib import asynccontextmanager
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from pymongo import ASCENDING, DESCENDING


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]
    app.state.db = db
    app.state.client = client
    await _create_indexes(db)
    yield
    client.close()


async def _create_indexes(db):
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
