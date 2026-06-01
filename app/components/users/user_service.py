from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import ADMIN_ROLES, UserRole
from app.common.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.common.utils import (
    build_pagination_meta,
    paginate_query,
    safe_regex,
    serialize_doc,
    serialize_docs,
    utcnow,
)
from app.components.auth.auth_service import hash_password, verify_password
from app.core.logging import logger

_ERR_USER_NOT_FOUND = "User not found"
_REGEX = "$regex"
_OPTIONS = "$options"


async def create_admin_user(db: AsyncIOMotorDatabase, data: dict) -> dict:
    """Create an admin or super_admin user and sync their workspace memberships.

    Super admins do not require workspace assignment.
    Workspace references are stored under admin_data.workspaces.

    Raises:
        ConflictException: If the email is already registered.
        ForbiddenException: If the role is not super_admin and no valid workspace is provided.
    """
    if await db.users.find_one({"email": data["email"]}):
        raise ConflictException("Email already registered")

    role = data.get("role", UserRole.ADMIN)
    workspace_ids = data.get("workspace_ids") or []
    workspaces = []

    if role != UserRole.SUPER_ADMIN:
        for wid in workspace_ids:
            ws = await db.workspaces.find_one({"_id": ObjectId(wid)})
            if ws:
                workspaces.append(ws)
        if not workspaces:
            raise ForbiddenException("At least one valid workspace must be assigned")

    now = utcnow()
    ws_ids_list = [str(ws["_id"]) for ws in workspaces]
    first_ws_id = ws_ids_list[0] if ws_ids_list else None

    doc = {
        "first_name": data["first_name"],
        "last_name": data.get("last_name") or "",
        "email": data["email"],
        "password_hash": hash_password(data["password"]),
        "role": role,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": ws_ids_list,
        "default_workspace_id": first_ws_id,
        "candidate_data": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(doc)
    user_id = result.inserted_id
    doc["_id"] = user_id
    doc.pop("password_hash")
    logger.info(f"Admin user created: {data['email']} role={role}")
    return serialize_doc(doc)


async def list_users(
    db: AsyncIOMotorDatabase,
    current_user_id: str,
    role_filter: str | None = None,
    is_active_filter: bool | None = None,
) -> list:
    """Return all admin/super_admin users, optionally filtered by role or active status."""
    query: dict = {"role": {"$in": ADMIN_ROLES}, "_id": {"$ne": ObjectId(current_user_id)}}
    if role_filter:
        query["role"] = role_filter
    if is_active_filter is not None:
        query["is_active"] = is_active_filter
    docs = await db.users.find(query, {"password_hash": 0}).to_list(500)
    return serialize_docs(docs)


async def get_user(db: AsyncIOMotorDatabase, user_id: str) -> dict:
    """Fetch a single user by ID, excluding the password hash.

    Raises:
        NotFoundException: If the user does not exist.
    """
    doc = await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0})
    if not doc:
        raise NotFoundException(_ERR_USER_NOT_FOUND)
    return serialize_doc(doc)


async def list_candidates(
    db: AsyncIOMotorDatabase,
    search: str | None = None,
    is_active: bool | None = None,
    candidate_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Return a paginated list of candidates, optionally filtered by search, status, or type."""
    query: dict = {"role": UserRole.CANDIDATE}
    if search:
        pattern = safe_regex(search)
        query["$or"] = [
            {"first_name": {_REGEX: pattern, _OPTIONS: "i"}},
            {"last_name": {_REGEX: pattern, _OPTIONS: "i"}},
            {"email": {_REGEX: pattern, _OPTIONS: "i"}},
        ]
    if is_active is not None:
        query["is_active"] = is_active
    if candidate_type:
        query["candidate_data.candidate_type"] = candidate_type

    skip, limit = paginate_query(page, page_size)
    total = await db.users.count_documents(query)
    docs = (
        await db.users.find(query, {"password_hash": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {
        "candidates": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }


async def get_candidate(db: AsyncIOMotorDatabase, user_id: str) -> dict:
    """Fetch a single candidate by ID.

    Raises:
        NotFoundException: If the user does not exist or is not a candidate.
    """
    doc = await db.users.find_one(
        {"_id": ObjectId(user_id), "role": UserRole.CANDIDATE}, {"password_hash": 0}
    )
    if not doc:
        raise NotFoundException("Candidate not found")
    return serialize_doc(doc)


async def update_candidate(db: AsyncIOMotorDatabase, user_id: str, data: dict) -> dict:
    """Super admin or admin update of a candidate's profile and candidate_data.

    Role and admin_data cannot be changed through this endpoint.

    Raises:
        NotFoundException: If the candidate does not exist.
        ConflictException: If a new email is already taken.
    """
    user = await db.users.find_one({"_id": ObjectId(user_id), "role": UserRole.CANDIDATE})
    if not user:
        raise NotFoundException("Candidate not found")

    update: dict = {}

    if data.get("first_name") is not None:
        update["first_name"] = data["first_name"]
    if data.get("last_name") is not None:
        update["last_name"] = data["last_name"]
    if data.get("email") is not None:
        await _apply_email_update(db, user_id, user, data["email"], update)
    if data.get("is_active") is not None:
        update["is_active"] = data["is_active"]

    candidate_data = data.get("candidate_data") or {}
    if isinstance(candidate_data, dict):
        cd = candidate_data
    else:
        cd = (
            candidate_data.model_dump(exclude_none=True)
            if hasattr(candidate_data, "model_dump")
            else {}
        )
    for field in ("candidate_type", "phone", "dob", "gender", "institution", "location"):
        if cd.get(field) is not None:
            update[f"candidate_data.{field}"] = cd[field]

    if update:
        update["updated_at"] = utcnow()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))


def _validate_super_admin_constraints(user: dict, data: dict) -> None:
    if user["role"] != UserRole.SUPER_ADMIN:
        return
    if data.get("role") is not None:
        raise ForbiddenException("Super admin role cannot be changed")
    if data.get("is_active") is not None:
        raise ForbiddenException("Super admin status cannot be changed")
    if data.get("workspace_ids") is not None:
        raise ForbiddenException("Super admin has access to all workspaces")


async def _apply_email_update(
    db: AsyncIOMotorDatabase, user_id: str, user: dict, new_email: str, update: dict
) -> None:
    if new_email == user["email"]:
        return
    if await db.users.find_one({"email": new_email, "_id": {"$ne": ObjectId(user_id)}}):
        raise ConflictException("Email already in use")
    update["email"] = new_email


async def _sync_workspace_memberships(
    db: AsyncIOMotorDatabase, user: dict, workspace_ids: list, update: dict
) -> None:
    existing = await db.workspaces.find(
        {"_id": {"$in": [ObjectId(wid) for wid in workspace_ids]}}, {"_id": 1}
    ).to_list(len(workspace_ids))
    valid_ids = [str(ws["_id"]) for ws in existing]

    update["workspace_ids"] = valid_ids
    current_default = user.get("default_workspace_id")
    if current_default not in valid_ids:
        update["default_workspace_id"] = valid_ids[0] if valid_ids else None


async def update_user(db: AsyncIOMotorDatabase, user_id: str, data: dict) -> dict:
    """Super admin update of any user's profile, status, role, email, password, or workspaces.

    Workspace membership in the workspaces collection is kept in sync automatically.
    Super admin status/workspaces cannot be changed.

    Raises:
        NotFoundException: If the user does not exist.
        ForbiddenException: If trying to change super admin status or workspaces.
        ConflictException: If a new email is already taken.
    """
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise NotFoundException(_ERR_USER_NOT_FOUND)

    _validate_super_admin_constraints(user, data)
    update: dict = {}

    if data.get("first_name") is not None:
        update["first_name"] = data["first_name"]
    if data.get("last_name") is not None:
        update["last_name"] = data["last_name"]

    if data.get("email") is not None:
        await _apply_email_update(db, user_id, user, data["email"], update)

    if data.get("role") is not None:
        update["role"] = data["role"]

    if data.get("password") is not None:
        update["password_hash"] = hash_password(data["password"])
        logger.info(f"Super admin reset password for user: {user.get('email')}")

    if data.get("is_active") is not None:
        update["is_active"] = data["is_active"]

    if data.get("workspace_ids") is not None:
        await _sync_workspace_memberships(db, user, data["workspace_ids"], update)

    if data.get("default_workspace_id") is not None:
        requested_default = data["default_workspace_id"]
        current_ws_ids = update.get("workspace_ids", user.get("workspace_ids", []))
        if requested_default in current_ws_ids:
            update["default_workspace_id"] = requested_default

    if update:
        update["updated_at"] = utcnow()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))


def _apply_password_change(user: dict, data: dict, update: dict) -> None:
    current_pw = data.get("current_password")
    if not current_pw:
        raise ValidationException("Current password is required to change password")
    if not verify_password(current_pw, user.get("password_hash", "")):
        raise UnauthorizedException("Current password is incorrect")
    update["password_hash"] = hash_password(data["password"])


async def _apply_default_workspace(
    db: AsyncIOMotorDatabase, user: dict, workspace_id: str, update: dict
) -> None:
    if not await db.workspaces.find_one({"_id": ObjectId(workspace_id)}):
        raise NotFoundException("Workspace not found")
    if user.get("role") != UserRole.SUPER_ADMIN:
        if workspace_id not in user.get("workspace_ids", []):
            raise ForbiddenException("You are not a member of this workspace")
    update["default_workspace_id"] = workspace_id


def _apply_candidate_data(data: dict, update: dict) -> None:
    raw = data.get("candidate_data") or {}
    if isinstance(raw, dict):
        cd = raw
    elif hasattr(raw, "model_dump"):
        cd = raw.model_dump(exclude_none=True)
    else:
        cd = {}
    for field in ("candidate_type", "phone", "dob", "gender", "institution", "location"):
        if cd.get(field) is not None:
            update[f"candidate_data.{field}"] = cd[field]


async def update_me(db: AsyncIOMotorDatabase, user_id: str, data: dict) -> dict:
    """Allow an authenticated user to update their own profile, email, password, or candidate data.

    Raises:
        NotFoundException: If the user or workspace does not exist.
        ForbiddenException: If not a member of the requested workspace (non-super_admin).
        UnauthorizedException: If current_password is wrong when changing password.
        ValidationException: If new password is provided without current_password.
        ConflictException: If the new email is already taken.
    """
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise NotFoundException(_ERR_USER_NOT_FOUND)

    update: dict = {}

    if data.get("first_name"):
        update["first_name"] = data["first_name"]
    if data.get("last_name") is not None:
        update["last_name"] = data["last_name"]

    if data.get("email") is not None:
        await _apply_email_update(db, user_id, user, data["email"], update)

    if data.get("password"):
        _apply_password_change(user, data, update)

    if data.get("default_workspace_id"):
        await _apply_default_workspace(db, user, data["default_workspace_id"], update)

    _apply_candidate_data(data, update)

    if update:
        update["updated_at"] = utcnow()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))
