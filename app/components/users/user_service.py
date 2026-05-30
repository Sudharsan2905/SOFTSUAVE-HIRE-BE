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
    ws_refs = [{"id": str(ws["_id"]), "name": ws["name"]} for ws in workspaces]
    first_ws_id = str(workspaces[0]["_id"]) if workspaces else None

    doc = {
        "first_name": data["first_name"],
        "last_name": data.get("last_name") or "",
        "email": data["email"],
        "password_hash": hash_password(data["password"]),
        "role": role,
        "is_active": True,
        "email_verified": False,
        "workspaces": ws_refs,
        "default_workspace_id": first_ws_id,
        "candidate_data": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(doc)
    user_id = result.inserted_id

    for ws in workspaces:
        existing_ids = {str(m["user_id"]) for m in ws.get("members", [])}
        if str(user_id) not in existing_ids:
            await db.workspaces.update_one(
                {"_id": ws["_id"]},
                {
                    "$push": {
                        "members": {"user_id": user_id, "email": data["email"], "role": role}
                    },
                    "$set": {"updated_at": utcnow()},
                },
            )

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
        raise NotFoundException("User not found")
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
            {"first_name": {"$regex": pattern, "$options": "i"}},
            {"last_name": {"$regex": pattern, "$options": "i"}},
            {"email": {"$regex": pattern, "$options": "i"}},
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
    await db.workspaces.update_many(
        {"members.user_id": ObjectId(user_id)},
        {"$set": {"members.$[m].email": new_email}},
        array_filters=[{"m.user_id": ObjectId(user_id)}],
    )


async def _sync_workspace_memberships(
    db: AsyncIOMotorDatabase, user_id: str, user: dict, workspace_ids: list, update: dict
) -> None:
    all_workspaces = await db.workspaces.find().to_list(500)
    ws_map = {str(ws["_id"]): ws for ws in all_workspaces}
    effective_role = update.get("role", user["role"])
    for ws in all_workspaces:
        ws_id_str = str(ws["_id"])
        member_ids = {str(m["user_id"]) for m in ws.get("members", [])}
        should_be_member = ws_id_str in workspace_ids
        is_member = user_id in member_ids
        if should_be_member and not is_member:
            await db.workspaces.update_one(
                {"_id": ws["_id"]},
                {
                    "$push": {
                        "members": {
                            "user_id": ObjectId(user_id),
                            "email": update.get("email", user["email"]),
                            "role": effective_role,
                        }
                    },
                    "$set": {"updated_at": utcnow()},
                },
            )
        elif not should_be_member and is_member:
            await db.workspaces.update_one(
                {"_id": ws["_id"]},
                {
                    "$pull": {"members": {"user_id": ObjectId(user_id)}},
                    "$set": {"updated_at": utcnow()},
                },
            )

    update["workspaces"] = [
        {"id": wid, "name": ws_map[wid]["name"]} for wid in workspace_ids if wid in ws_map
    ]
    current_default = user.get("default_workspace_id")
    if current_default not in workspace_ids:
        update["default_workspace_id"] = workspace_ids[0] if workspace_ids else None


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
        raise NotFoundException("User not found")

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
        await db.workspaces.update_many(
            {"members.user_id": ObjectId(user_id)},
            {"$set": {"members.$[m].role": data["role"]}},
            array_filters=[{"m.user_id": ObjectId(user_id)}],
        )

    if data.get("password") is not None:
        update["password_hash"] = hash_password(data["password"])
        logger.info(f"Super admin reset password for user: {user.get('email')}")

    if data.get("is_active") is not None:
        update["is_active"] = data["is_active"]

    if data.get("workspace_ids") is not None:
        await _sync_workspace_memberships(db, user_id, user, data["workspace_ids"], update)

    if data.get("default_workspace_id") is not None:
        requested_default = data["default_workspace_id"]
        current_workspaces = update.get("workspaces", user.get("workspaces", []))
        final_ws_ids = [w["id"] for w in current_workspaces]
        if requested_default in final_ws_ids:
            update["default_workspace_id"] = requested_default

    if update:
        update["updated_at"] = utcnow()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))


async def _apply_password_change(user: dict, data: dict, update: dict) -> None:
    current_pw = data.get("current_password")
    if not current_pw:
        raise ValidationException("Current password is required to change password")
    if not verify_password(current_pw, user.get("password_hash", "")):
        raise UnauthorizedException("Current password is incorrect")
    update["password_hash"] = hash_password(data["password"])


async def _apply_default_workspace(
    db: AsyncIOMotorDatabase, user: dict, user_id: str, workspace_id: str, update: dict
) -> None:
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException("Workspace not found")
    if user.get("role") != UserRole.SUPER_ADMIN:
        member_ids = [str(m["user_id"]) for m in workspace.get("members", [])]
        if user_id not in member_ids:
            raise ForbiddenException("You are not a member of this workspace")
    update["default_workspace_id"] = workspace_id


def _apply_candidate_data(data: dict, update: dict) -> None:
    raw = data.get("candidate_data") or {}
    cd = (
        raw
        if isinstance(raw, dict)
        else (raw.model_dump(exclude_none=True) if hasattr(raw, "model_dump") else {})
    )
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
        raise NotFoundException("User not found")

    update: dict = {}

    if data.get("first_name"):
        update["first_name"] = data["first_name"]
    if data.get("last_name") is not None:
        update["last_name"] = data["last_name"]

    if data.get("email") is not None:
        await _apply_email_update(db, user_id, user, data["email"], update)

    if data.get("password"):
        await _apply_password_change(user, data, update)

    if data.get("default_workspace_id"):
        await _apply_default_workspace(db, user, user_id, data["default_workspace_id"], update)

    _apply_candidate_data(data, update)

    if update:
        update["updated_at"] = utcnow()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))
