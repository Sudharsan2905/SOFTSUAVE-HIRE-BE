from datetime import timedelta

import bcrypt
import httpx
from jose import JWTError, jwt
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import ADMIN_ROLES, CandidateType, UserRole
from app.common.exceptions import (
    ConflictException,
    ForbiddenException,
    UnauthorizedException,
)
from app.common.utils import generate_secure_token, hash_token, serialize_doc, utcnow
from app.core.config import settings
from app.core.logging import logger


def verify_password(plain: str, hashed: str) -> bool:
    return bool(bcrypt.checkpw(plain.encode(), hashed.encode()))


def hash_password(password: str) -> str:
    return str(bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())


def create_access_token(data: dict) -> str:
    expire = utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {**data, "exp": expire, "type": "access"},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise UnauthorizedException("Invalid token type")
        return payload
    except JWTError as exc:
        logger.warning(f"Token decode failed: {exc}")
        raise UnauthorizedException("Invalid or expired token")


async def _issue_tokens(db: AsyncIOMotorDatabase, user: dict) -> dict:
    """Create and persist an access/refresh token pair for the given user."""
    user_data = serialize_doc(user)
    user_data.pop("password_hash", None)

    access_token = create_access_token(
        {"sub": str(user["_id"]), "role": user["role"], "email": user["email"]}
    )
    refresh_token_raw = generate_secure_token()
    expires_at = utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    await db.refresh_tokens.insert_one(
        {
            "user_id": user["_id"],
            "token_hash": hash_token(refresh_token_raw),
            "expires_at": expires_at,
            "created_at": utcnow(),
        }
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token_raw,
        "token_type": "bearer",
        "user": user_data,
    }


async def admin_login(db: AsyncIOMotorDatabase, email: str, password: str) -> dict:
    """Authenticate a super_admin or admin and issue JWT tokens.

    Raises:
        UnauthorizedException: On invalid credentials or deactivated account.
    """
    user = await db.users.find_one({"email": email, "role": {"$in": ADMIN_ROLES}})
    if not user or not verify_password(password, user.get("password_hash", "")):
        logger.warning(f"Failed admin login attempt for email: {email}")
        raise UnauthorizedException("Invalid email or password")
    if not user.get("is_active", True):
        logger.warning(f"Deactivated admin attempted login: {email}")
        raise UnauthorizedException("Your account has been deactivated. Contact your super admin.")
    logger.info(f"Admin logged in: {email}")
    return await _issue_tokens(db, user)


async def candidate_login(db: AsyncIOMotorDatabase, email: str, password: str) -> dict:
    """Authenticate a candidate and issue JWT tokens.

    Raises:
        UnauthorizedException: On invalid credentials or deactivated account.
    """
    user = await db.users.find_one({"email": email, "role": UserRole.CANDIDATE})
    if not user or not verify_password(password, user.get("password_hash", "")):
        logger.warning(f"Failed candidate login attempt for email: {email}")
        raise UnauthorizedException("Invalid email or password")
    if not user.get("is_active", True):
        logger.warning(f"Deactivated candidate attempted login: {email}")
        raise UnauthorizedException("Your account has been deactivated.")
    logger.info(f"Candidate logged in: {email}")
    return await _issue_tokens(db, user)


async def register_candidate(db: AsyncIOMotorDatabase, data: dict) -> dict:
    """Register a new candidate and issue JWT tokens.

    candidate_data (phone, gender, dob, candidate_type, institution, location) is stored
    as a nested subdocument on the user. Google-linked registrations set email_verified=True.

    Supports Google-linked registrations when google_id is provided.

    Raises:
        ConflictException: If the email is already registered.
    """
    if await db.users.find_one({"email": data["email"]}):
        raise ConflictException("Email already registered")
    logger.info(f"Registering new candidate: {data['email']}")

    raw_password = data.pop("password", None)
    google_id = data.pop("google_id", None)
    data.pop("assessment_uuid", None)

    if not google_id and not raw_password:
        from app.common.exceptions import ValidationException

        raise ValidationException("Password is required for email registration")

    password_hash = hash_password(raw_password) if raw_password else None
    now = utcnow()

    user_doc = {
        "first_name": data["first_name"],
        "last_name": data.get("last_name") or "",
        "email": data["email"],
        "password_hash": password_hash,
        "role": UserRole.CANDIDATE,
        "is_active": True,
        "email_verified": bool(google_id),
        "workspace_ids": [],
        "default_workspace_id": None,
        "candidate_data": {
            "candidate_type": data.get("candidate_type", CandidateType.STUDENT),
            "google_id": google_id,
            "phone": data.get("phone"),
            "dob": data.get("dob"),
            "gender": data.get("gender"),
            "institution": data.get("institution"),
            "location": data.get("location"),
        },
        "created_at": now,
        "updated_at": now,
    }

    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return await _issue_tokens(db, user_doc)


async def refresh_access_token(db: AsyncIOMotorDatabase, refresh_token: str) -> dict:
    token_hash = hash_token(refresh_token)
    token_doc = await db.refresh_tokens.find_one({"token_hash": token_hash})

    expires_at = token_doc["expires_at"] if token_doc else None
    if expires_at is not None and expires_at.tzinfo is None:
        from datetime import UTC

        expires_at = expires_at.replace(tzinfo=UTC)
    if not token_doc or expires_at < utcnow():
        if token_doc:
            await db.refresh_tokens.delete_one({"_id": token_doc["_id"]})
        raise UnauthorizedException("Refresh token expired or invalid")

    user = await db.users.find_one({"_id": token_doc["user_id"]})
    if not user:
        raise UnauthorizedException("User not found")

    access_token = create_access_token(
        {"sub": str(user["_id"]), "role": user["role"], "email": user["email"]}
    )
    return {"access_token": access_token, "token_type": "bearer"}


async def logout(db: AsyncIOMotorDatabase, refresh_token: str) -> None:
    await db.refresh_tokens.delete_one({"token_hash": hash_token(refresh_token)})


async def google_auth(db: AsyncIOMotorDatabase, credential: str) -> dict:
    """Validate a Google ID token and either issue tokens (existing user) or return
    pre-auth data for registration (new user).

    For existing candidates: issues JWT tokens directly.
    For new users: returns { needs_registration: True, google_data: {...} } without
    creating any account — the caller must complete registration via /auth/register.

    Raises:
        UnauthorizedException: On invalid or mismatched Google token.
        ForbiddenException: If the account exists but has a non-candidate role.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": credential},
        )

    if resp.status_code != 200:
        raise UnauthorizedException("Invalid Google credential")

    info = resp.json()
    if info.get("aud") != settings.GOOGLE_CLIENT_ID:
        logger.warning("Google OAuth token audience mismatch")
        raise UnauthorizedException("Google token audience mismatch")

    email = info.get("email")
    if not email:
        raise UnauthorizedException("Email not found in Google token")

    user = await db.users.find_one({"email": email})

    if not user:
        logger.info(f"Google OAuth pre-auth (new user): {email}")
        return {
            "needs_registration": True,
            "google_data": {
                "email": email,
                "first_name": info.get("given_name") or info.get("name", ""),
                "last_name": info.get("family_name", ""),
                "google_id": info.get("sub", ""),
                "picture": info.get("picture", ""),
            },
        }

    if user.get("role") != UserRole.CANDIDATE:
        raise ForbiddenException("This Google account is not registered as a candidate")

    logger.info(f"Google OAuth login (existing candidate): {email}")
    return await _issue_tokens(db, user)


async def setup_super_admin(db: AsyncIOMotorDatabase, data: dict) -> dict:
    if await db.users.find_one({"role": UserRole.SUPER_ADMIN}):
        raise ForbiddenException("Setup already complete. A super admin already exists.")

    now = utcnow()

    user_doc = {
        "first_name": data["first_name"],
        "last_name": data.get("last_name") or "",
        "email": data["email"],
        "password_hash": hash_password(data["password"]),
        "role": UserRole.SUPER_ADMIN,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": [],
        "default_workspace_id": None,
        "candidate_data": None,
        "created_at": now,
        "updated_at": now,
    }
    user_result = await db.users.insert_one(user_doc)
    user_doc["_id"] = user_result.inserted_id
    return await _issue_tokens(db, user_doc)
