from datetime import timedelta

import bcrypt
import httpx
from jose import JWTError, jwt
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import UserRole
from app.common.exceptions import (
    ConflictException,
    ForbiddenException,
    UnauthorizedException,
)
from app.common.utils import generate_secure_token, hash_token, serialize_doc, utcnow
from app.core.config import settings


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


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
    except JWTError:
        raise UnauthorizedException("Invalid or expired token")


async def _issue_tokens(db: AsyncIOMotorDatabase, user: dict) -> dict:
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
    user = await db.users.find_one({"email": email, "role": {"$in": ["super_admin", "admin"]}})
    if not user or not verify_password(password, user.get("password_hash", "")):
        raise UnauthorizedException("Invalid email or password")
    if not user.get("is_active", True):
        raise UnauthorizedException("Your account has been deactivated. Contact your super admin.")
    return await _issue_tokens(db, user)


async def candidate_login(db: AsyncIOMotorDatabase, email: str, password: str) -> dict:
    user = await db.users.find_one({"email": email, "role": UserRole.CANDIDATE})
    if not user or not verify_password(password, user.get("password_hash", "")):
        raise UnauthorizedException("Invalid email or password")

    result = await _issue_tokens(db, user)
    candidate = await db.candidates.find_one({"user_id": user["_id"]})
    if candidate:
        result["user"]["profile"] = serialize_doc(candidate)
    return result


async def register_candidate(db: AsyncIOMotorDatabase, data: dict) -> dict:
    if await db.users.find_one({"email": data["email"]}):
        raise ConflictException("Email already registered")

    password_hash = hash_password(data.pop("password"))
    data.pop("assessment_uuid", None)
    now = utcnow()

    user_doc = {
        "email": data["email"],
        "first_name": data["first_name"],
        "last_name": data.get("last_name") or "",
        "role": UserRole.CANDIDATE,
        "password_hash": password_hash,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(user_doc)
    user_id = result.inserted_id

    await db.candidates.insert_one(
        {
            "user_id": user_id,
            "phone": data.get("phone"),
            "father_name": data.get("father_name"),
            "gender": data.get("gender"),
            "dob": data.get("dob"),
            "college_name": data.get("college_name"),
            "college_city": data.get("college_city"),
            "created_at": now,
            "updated_at": now,
        }
    )

    user_doc["_id"] = user_id
    return await _issue_tokens(db, user_doc)


async def refresh_access_token(db: AsyncIOMotorDatabase, refresh_token: str) -> dict:
    token_hash = hash_token(refresh_token)
    token_doc = await db.refresh_tokens.find_one({"token_hash": token_hash})

    if not token_doc or token_doc["expires_at"] < utcnow():
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


async def logout(db: AsyncIOMotorDatabase, refresh_token: str):
    await db.refresh_tokens.delete_one({"token_hash": hash_token(refresh_token)})


async def google_auth(db: AsyncIOMotorDatabase, credential: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": credential},
        )

    if resp.status_code != 200:
        raise UnauthorizedException("Invalid Google credential")

    info = resp.json()
    if info.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise UnauthorizedException("Google token audience mismatch")

    email = info.get("email")
    if not email:
        raise UnauthorizedException("Email not found in Google token")

    user = await db.users.find_one({"email": email})
    now = utcnow()

    if not user:
        user_doc = {
            "email": email,
            "first_name": info.get("given_name") or info.get("name", "User"),
            "last_name": info.get("family_name", ""),
            "role": UserRole.CANDIDATE,
            "google_id": info.get("sub"),
            "password_hash": "",
            "created_at": now,
            "updated_at": now,
        }
        result = await db.users.insert_one(user_doc)
        user_id = result.inserted_id

        await db.candidates.insert_one({"user_id": user_id, "created_at": now, "updated_at": now})

        user_doc["_id"] = user_id
        user = user_doc
    elif user.get("role") != UserRole.CANDIDATE:
        raise ForbiddenException("This Google account is not registered as a candidate")

    result = await _issue_tokens(db, user)
    candidate = await db.candidates.find_one({"user_id": user["_id"]})
    if candidate:
        result["user"]["profile"] = serialize_doc(candidate)
    return result


async def setup_super_admin(db: AsyncIOMotorDatabase, data: dict) -> dict:
    if await db.users.find_one({"role": "super_admin"}):
        raise ForbiddenException("Setup already complete. A super admin already exists.")

    now = utcnow()

    user_doc = {
        "first_name": data["first_name"],
        "last_name": data.get("last_name") or "",
        "email": data["email"],
        "password_hash": hash_password(data["password"]),
        "role": "super_admin",
        "is_active": True,
        "workspaces": [],
        "default_workspace_id": None,
        "created_at": now,
        "updated_at": now,
    }
    user_result = await db.users.insert_one(user_doc)
    user_id = user_result.inserted_id
    user_doc["_id"] = user_id
    return await _issue_tokens(db, user_doc)
