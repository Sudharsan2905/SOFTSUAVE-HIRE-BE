import asyncio
from datetime import timedelta

import bcrypt
import httpx
from jose import JWTError, jwt
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import ADMIN_ROLES, CandidateType, UserRole
from app.common.constants.messages import ErrorMessages
from app.common.exceptions import (
    ConflictException,
    ForbiddenException,
    UnauthorizedException,
)
from app.common.response_models.auth_responses import (
    AuthTokenResponse,
    GooglePreAuthResponse,
    GoogleUserData,
    TokenRefreshResponse,
    UserInTokenResponse,
)
from app.common.utils import (
    email_query,
    generate_secure_token,
    hash_token,
    normalize_email,
    serialize_doc,
    utcnow,
)
from app.components.candidate.candidate_service import _check_candidate_access_restriction
from app.core.config import settings
from app.core.logging import logger


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:  # password-less (e.g. Google-only) account
        return False
    try:
        return bool(bcrypt.checkpw(plain.encode(), hashed.encode()))
    except ValueError:  # malformed hash
        return False


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


async def _issue_tokens(db: AsyncIOMotorDatabase, user: dict) -> AuthTokenResponse:
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
    return AuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token_raw,
        token_type="bearer",  # noqa: S106  # nosec B106
        user=UserInTokenResponse.model_validate(user_data),
    )


async def admin_login(db: AsyncIOMotorDatabase, email: str, password: str) -> AuthTokenResponse:
    """Authenticate a super_admin or admin and issue JWT tokens.

    Raises:
        UnauthorizedException: On invalid credentials or deactivated account.
    """
    user = await db.users.find_one({"email": email_query(email), "role": {"$in": ADMIN_ROLES}})
    if not user or not await asyncio.to_thread(
        verify_password, password, user.get("password_hash", "")
    ):
        logger.warning(f"Failed admin login attempt for email: {email}")
        raise UnauthorizedException(ErrorMessages.INVALID_CREDENTIALS)
    if not user.get("is_active", True):
        logger.warning(f"Deactivated admin attempted login: {email}")
        raise UnauthorizedException("Your account has been deactivated. Contact your super admin.")
    logger.info(f"Admin logged in: {email}")
    return await _issue_tokens(db, user)


async def candidate_login(
    db: AsyncIOMotorDatabase,
    email: str,
    password: str,
    share_link: str | None = None,
) -> AuthTokenResponse:
    """Authenticate a candidate and issue JWT tokens.

    Raises:
        UnauthorizedException: On invalid credentials or deactivated account.
        ForbiddenException: If the share link's candidate restriction blocks this candidate.
    """
    user = await db.users.find_one({"email": email_query(email), "role": UserRole.CANDIDATE})
    if not user or not await asyncio.to_thread(
        verify_password, password, user.get("password_hash", "")
    ):
        logger.warning(f"Failed candidate login attempt for email: {email}")
        raise UnauthorizedException(ErrorMessages.INVALID_CREDENTIALS)
    if not user.get("is_active", True):
        logger.warning(f"Deactivated candidate attempted login: {email}")
        raise UnauthorizedException("Your account has been deactivated.")
    if share_link:
        share_doc = await db.assessment_shares.find_one(
            {"share_link": share_link, "is_active": True}
        )
        if share_doc:
            await _check_candidate_access_restriction(db, share_doc, str(user["_id"]))
    logger.info(f"Candidate logged in: {email}")
    return await _issue_tokens(db, user)


async def register_candidate(db: AsyncIOMotorDatabase, data: dict) -> AuthTokenResponse:
    """Register a new candidate and issue JWT tokens.

    Candidate fields (phone, gender, dob, candidate_type, institution, location, google_id)
    are stored flat on the user document. Google-linked registrations set email_verified=True.

    Supports Google-linked registrations when google_id is provided.

    Raises:
        ConflictException: If the email is already registered.
    """
    data["email"] = normalize_email(data["email"])
    if await db.users.find_one({"email": email_query(data["email"])}):
        raise ConflictException(ErrorMessages.EMAIL_TAKEN)
    logger.info(f"Registering new candidate: {data['email']}")

    raw_password = data.pop("password", None)
    claimed_google_id = data.pop("google_id", None)
    data.pop("assessment_uuid", None)

    # The client may NOT assert a Google identity: ``google_id`` must be the short-lived,
    # server-signed registration token issued by google_auth() after it verified the
    # Google ID token. Anything else (a bare id) is rejected, which prevents pre-hijacking
    # a victim's address with an attacker-chosen password and a forged google_id.
    google_id: str | None = None
    if claimed_google_id:
        google_id = _verify_google_registration_token(claimed_google_id, data["email"])

    if not google_id and not raw_password:
        from app.common.exceptions import ValidationException

        raise ValidationException("Password is required for email registration")

    password_hash = await asyncio.to_thread(hash_password, raw_password) if raw_password else None
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
        "candidate_type": data.get("candidate_type", CandidateType.STUDENT),
        "google_id": google_id,
        "phone": data.get("phone"),
        "dob": data.get("dob"),
        "gender": data.get("gender"),
        "institution": data.get("institution"),
        "location": data.get("location"),
        "created_at": now,
        "updated_at": now,
    }

    result = await db.users.insert_one(user_doc)
    user_doc["_id"] = result.inserted_id
    return await _issue_tokens(db, user_doc)


_GOOGLE_REG_TOKEN_TYPE = "google_registration"  # noqa: S105  # nosec B105
_GOOGLE_REG_TOKEN_TTL_MINUTES = 15


def _create_google_registration_token(email: str, google_sub: str) -> str:
    return str(
        jwt.encode(
            {
                "type": _GOOGLE_REG_TOKEN_TYPE,
                "email": email,
                "gid": google_sub,
                "exp": utcnow() + timedelta(minutes=_GOOGLE_REG_TOKEN_TTL_MINUTES),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
    )


def _verify_google_registration_token(token: str, email: str) -> str:
    """Return the verified Google subject id, or raise if the token is bad / for another email."""
    from app.common.exceptions import ValidationException

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValidationException("Google sign-up session is invalid or has expired") from exc
    if payload.get("type") != _GOOGLE_REG_TOKEN_TYPE or payload.get("email") != email:
        raise ValidationException("Google sign-up session does not match this email")
    gid = payload.get("gid")
    if not gid:
        raise ValidationException("Google sign-up session is invalid")
    return str(gid)


async def revoke_user_tokens(db: AsyncIOMotorDatabase, user_id: str | object) -> int:
    """Delete every refresh token of a user (password/role change, deactivation, deletion)."""
    from bson import ObjectId

    uid = user_id if isinstance(user_id, ObjectId) else ObjectId(str(user_id))
    result = await db.refresh_tokens.delete_many({"user_id": uid})
    return int(result.deleted_count)


async def refresh_access_token(
    db: AsyncIOMotorDatabase, refresh_token: str
) -> TokenRefreshResponse:
    """Exchange a refresh token for a new access token **and a new refresh token**.

    Refresh tokens are single-use: the presented one is atomically deleted, so a stolen
    token that has already been used (or a replay of it) is rejected.
    """
    token_hash = hash_token(refresh_token)
    # Atomic claim: only one concurrent caller can consume a given token.
    token_doc = await db.refresh_tokens.find_one_and_delete({"token_hash": token_hash})
    if not token_doc:
        raise UnauthorizedException("Refresh token expired or invalid")

    expires_at = token_doc["expires_at"]
    if expires_at.tzinfo is None:
        from datetime import UTC

        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < utcnow():
        raise UnauthorizedException("Refresh token expired or invalid")

    user = await db.users.find_one({"_id": token_doc["user_id"]})
    if not user:
        raise UnauthorizedException("User not found")
    if not user.get("is_active", True):
        await revoke_user_tokens(db, user["_id"])
        raise UnauthorizedException("Your account has been deactivated.")

    access_token = create_access_token(
        {"sub": str(user["_id"]), "role": user["role"], "email": user["email"]}
    )
    new_refresh_raw = generate_secure_token()
    await db.refresh_tokens.insert_one(
        {
            "user_id": user["_id"],
            "token_hash": hash_token(new_refresh_raw),
            "expires_at": utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            "created_at": utcnow(),
        }
    )
    return TokenRefreshResponse(
        access_token=access_token,
        token_type="bearer",  # noqa: S106  # nosec B106
        refresh_token=new_refresh_raw,
    )


async def logout(
    db: AsyncIOMotorDatabase, refresh_token: str, caller_id: str | None = None
) -> None:
    """Revoke a refresh token.

    The token itself proves ownership; if the caller also presents a valid access token,
    it must belong to the same user (so one user cannot revoke another's session).
    """
    token_hash = hash_token(refresh_token)
    if caller_id is not None:
        doc = await db.refresh_tokens.find_one({"token_hash": token_hash}, {"user_id": 1})
        if doc and str(doc["user_id"]) != str(caller_id):
            raise ForbiddenException("This refresh token does not belong to you")
    await db.refresh_tokens.delete_one({"token_hash": token_hash})


async def google_auth(
    db: AsyncIOMotorDatabase, credential: str
) -> AuthTokenResponse | GooglePreAuthResponse:
    """Validate a Google ID token and either issue tokens (existing user) or return
    pre-auth data for registration (new user).

    For existing candidates: issues JWT tokens directly.
    For new users: returns { needs_registration: True, google_data: {...} } without
    creating any account — the caller must complete registration via /auth/register,
    passing ``google_data.google_id`` (a short-lived signed registration token).

    Raises:
        UnauthorizedException: On invalid/mismatched token or an unverified Google email.
        ForbiddenException: If the account exists but has a non-candidate role.
    """
    async with httpx.AsyncClient(timeout=10) as client:
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

    email = normalize_email(info.get("email"))
    if not email:
        raise UnauthorizedException("Email not found in Google token")
    # Google reports this as the string "true"; never trust an unverified address.
    if str(info.get("email_verified", "")).lower() != "true":
        logger.warning("Google OAuth rejected: email not verified")
        raise UnauthorizedException("Your Google email address is not verified")

    user = await db.users.find_one({"email": email_query(email)})

    if not user:
        logger.info(f"Google OAuth pre-auth (new user): {email}")
        return GooglePreAuthResponse(
            needs_registration=True,
            google_data=GoogleUserData(
                email=email,
                first_name=info.get("given_name") or info.get("name", ""),
                last_name=info.get("family_name", ""),
                # Signed, short-lived registration token (carries the verified Google
                # subject + email). The client echoes it back as `google_id`.
                google_id=_create_google_registration_token(email, info.get("sub", "")),
                picture=info.get("picture", ""),
            ),
        )

    if user.get("role") != UserRole.CANDIDATE:
        raise ForbiddenException("This Google account is not registered as a candidate")
    if not user.get("is_active", True):
        raise UnauthorizedException("Your account has been deactivated.")

    logger.info(f"Google OAuth login (existing candidate): {email}")
    return await _issue_tokens(db, user)


_SETUP_MARKER_ID = "super_admin_setup"


async def setup_super_admin(db: AsyncIOMotorDatabase, data: dict) -> AuthTokenResponse:
    """Create the first super admin. Atomic: concurrent calls can never create two.

    A unique-``_id`` marker document is claimed first; only the caller that inserts it
    proceeds.  (A plain "check then insert" lets two parallel requests both pass the check.)
    """
    from pymongo.errors import DuplicateKeyError

    if await db.users.find_one({"role": UserRole.SUPER_ADMIN}, {"_id": 1}):
        raise ForbiddenException("Setup already complete. A super admin already exists.")

    try:
        await db.app_meta.insert_one({"_id": _SETUP_MARKER_ID, "claimed_at": utcnow()})
    except DuplicateKeyError as exc:
        raise ForbiddenException("Setup already complete. A super admin already exists.") from exc

    try:
        now = utcnow()
        user_doc = {
            "first_name": data["first_name"],
            "last_name": data.get("last_name") or "",
            "email": normalize_email(data["email"]),
            "password_hash": await asyncio.to_thread(hash_password, data["password"]),
            "role": UserRole.SUPER_ADMIN,
            "is_active": True,
            "email_verified": False,
            "workspace_ids": [],
            "default_workspace_id": None,
            "created_at": now,
            "updated_at": now,
        }
        user_result = await db.users.insert_one(user_doc)
    except Exception:
        await db.app_meta.delete_one({"_id": _SETUP_MARKER_ID})  # let setup be retried
        raise
    user_doc["_id"] = user_result.inserted_id
    return await _issue_tokens(db, user_doc)
