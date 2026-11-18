"""Candidate provisioning: create (or reuse) a candidate user with a fresh temporary password."""

import secrets
import string

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import CandidateType, UserRole
from app.common.utils import utcnow
from app.components.auth.auth_service import hash_password
from app.core.logging import logger

_LOWER = "abcdefghijkmnpqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
_SPECIAL = "@#$%"


def generate_temp_password(length: int = 12) -> str:
    """Readable temp password that satisfies the platform's password-strength rules."""
    chars = [secrets.choice(_LOWER), secrets.choice(_UPPER), secrets.choice(_DIGITS), secrets.choice(_SPECIAL)]
    pool = _LOWER + _UPPER + _DIGITS + _SPECIAL
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _split_name(name: str | None, email: str) -> tuple[str, str]:
    full = (name or "").strip() or email.split("@")[0]
    first, _, last = full.partition(" ")
    return first[:50], last.strip()[:50]


async def provision_candidate(
    db: AsyncIOMotorDatabase, *, email: str, name: str | None, phone: str | None = None
) -> tuple[ObjectId, str]:
    """Return ``(user_id, temp_password)``; reuses an existing candidate account (password reset).

    Raises ValueError if the email belongs to a non-candidate account (never modified).
    """
    email = email.strip().lower()
    password = generate_temp_password()
    now = utcnow()
    existing = await db.users.find_one({"email": email})
    if existing:
        if existing.get("role") != UserRole.CANDIDATE.value:
            raise ValueError("This email belongs to a non-candidate account; not modified")
        await db.users.update_one(
            {"_id": existing["_id"]},
            {"$set": {"password_hash": hash_password(password), "is_active": True, "updated_at": now}},
        )
        logger.info(f"Reused existing candidate account: {email}")
        return existing["_id"], password
    first, last = _split_name(name, email)
    doc = {
        "first_name": first,
        "last_name": last,
        "email": email,
        "password_hash": hash_password(password),
        "role": UserRole.CANDIDATE.value,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": [],
        "default_workspace_id": None,
        "candidate_type": CandidateType.PROFESSIONAL.value,
        "google_id": None,
        "phone": phone,
        "dob": None,
        "gender": None,
        "institution": None,
        "location": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(doc)
    logger.info(f"Created candidate account: {email}")
    return result.inserted_id, password
