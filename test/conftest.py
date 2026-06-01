"""Shared pytest fixtures for all tests."""

import os

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.common.constants.app_constants import UserRole
from app.common.utils import utcnow
from app.components.auth.auth_service import hash_password
from app.core.limiter import limiter as _limiter  # noqa: E402

os.environ["RATELIMIT_ENABLED"] = "0"  # must be set before any app module is imported
_limiter.limit = lambda *args, **kwargs: (lambda f: f)


@pytest.fixture
def db():
    """In-memory MongoDB instance reset for each test."""
    client = AsyncMongoMockClient()
    return client["test_db"]


@pytest.fixture
async def super_admin(db):
    """Pre-seeded super admin user document."""
    doc = {
        "first_name": "Super",
        "last_name": "Admin",
        "email": "superadmin@example.com",
        "password_hash": hash_password("SuperPass@123"),
        "role": UserRole.SUPER_ADMIN,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": [],
        "default_workspace_id": None,
        "candidate_data": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def admin_user(db, workspace):
    """Pre-seeded admin user linked to a workspace."""
    doc = {
        "first_name": "Admin",
        "last_name": "User",
        "email": "admin@example.com",
        "password_hash": hash_password("AdminPass@123"),
        "role": UserRole.ADMIN,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": [str(workspace["_id"])],
        "default_workspace_id": str(workspace["_id"]),
        "candidate_data": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def candidate_user(db):
    """Pre-seeded candidate user document."""
    doc = {
        "first_name": "Test",
        "last_name": "Candidate",
        "email": "candidate@example.com",
        "password_hash": hash_password("CandPass@123"),
        "role": UserRole.CANDIDATE,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": [],
        "default_workspace_id": None,
        "candidate_data": {
            "candidate_type": "student",
            "google_id": None,
            "phone": "9999999999",
            "dob": None,
            "gender": "male",
            "institution": None,
            "location": None,
        },
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def workspace(db, super_admin):
    """Pre-seeded workspace document."""
    doc = {
        "name": "Test Workspace",
        "description": "A workspace for testing",
        "created_by": super_admin["_id"],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await db.workspaces.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def category(db, super_admin):
    """Pre-seeded question category."""
    doc = {
        "name": "Python",
        "description": "Python questions",
        "created_by": super_admin["_id"],
        "question_count": 0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await db.question_categories.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def mcq_question(db, category, super_admin):
    """Pre-seeded MCQ single-choice question."""
    doc = {
        "category_id": category["_id"],
        "question_text": "What is 2 + 2?",
        "question_type": "mcq_single",
        "complexity": "low",
        "options": [
            {"id": "a", "text": "3", "is_correct": False},
            {"id": "b", "text": "4", "is_correct": True},
            {"id": "c", "text": "5", "is_correct": False},
        ],
        "correct_answer": None,
        "created_by": super_admin["_id"],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await db.questions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc
