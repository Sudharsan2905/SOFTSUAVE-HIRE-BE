from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import UserRole
from app.common.constants.messages import ErrorMessages
from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.response_models.user_responses import AdminUserResponse
from app.common.response_models.workspace_responses import WorkspaceResponse
from app.common.utils import (
    build_pagination_meta,
    list_paginated,
    paginate_query,
    serialize_doc,
    utcnow,
)
from app.core.logging import logger

_PUSH = "$push"

_WORKSPACE_PROJECTION = {
    "name": 1,
    "description": 1,
    "created_by": 1,
    "created_at": 1,
    "updated_at": 1,
}
_MEMBER_PROJECTION = {
    "first_name": 1,
    "last_name": 1,
    "email": 1,
    "role": 1,
    "is_active": 1,
    "workspace_ids": 1,
    "default_workspace_id": 1,
    "created_at": 1,
}


async def create_workspace(
    db: AsyncIOMotorDatabase, data: dict, created_by: str
) -> WorkspaceResponse:
    """Create a new workspace and set it as the creator's default if they have none yet.

    Admins get the workspace ID added to their workspace_ids list.
    Super admins keep workspace_ids empty — they have implicit access to all workspaces.
    """
    now = utcnow()
    doc = {
        "name": data["name"],
        "description": data.get("description", ""),
        "created_by": ObjectId(created_by),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.workspaces.insert_one(doc)
    workspace_id = str(result.inserted_id)
    doc["_id"] = result.inserted_id

    creator = await db.users.find_one({"_id": ObjectId(created_by)})
    if creator:
        set_fields: dict = {"updated_at": utcnow()}
        if not creator.get("default_workspace_id"):
            set_fields["default_workspace_id"] = workspace_id
        if creator.get("role") == UserRole.SUPER_ADMIN:
            await db.users.update_one({"_id": ObjectId(created_by)}, {"$set": set_fields})
        else:
            await db.users.update_one(
                {"_id": ObjectId(created_by), "workspace_ids": {"$ne": workspace_id}},
                {_PUSH: {"workspace_ids": workspace_id}, "$set": set_fields},
            )

    logger.info(f"Workspace created: {data['name']} by user_id={created_by}")
    return WorkspaceResponse.model_validate(serialize_doc(doc))


async def get_workspaces(
    db: AsyncIOMotorDatabase,
    user_role: str,
    user_workspace_ids: list,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Return a paginated list of workspaces visible to the requesting user.

    Super admins see all workspaces.
    Admins see only their assigned workspaces (sourced from user.workspace_ids).
    """
    skip, limit = paginate_query(page, page_size)
    if user_role == UserRole.SUPER_ADMIN:
        query: dict = {}
    else:
        ws_ids = [ObjectId(wid) for wid in user_workspace_ids if wid]
        query = {"_id": {"$in": ws_ids}}

    total, docs = await list_paginated(
        db.workspaces, query, "created_at", -1, skip, limit, ["created_at"]
    )
    return {
        "workspaces": [WorkspaceResponse.model_validate(serialize_doc(d)) for d in docs],
        "pagination": build_pagination_meta(total, page, page_size),
    }


async def get_workspace(
    db: AsyncIOMotorDatabase,
    workspace_id: str,
    user_role: str,
    user_workspace_ids: list,
) -> WorkspaceResponse:
    """Fetch a single workspace, enforcing membership access for non-super_admin users.

    Raises:
        NotFoundException: If the workspace does not exist.
        ForbiddenException: If the user does not have access.
    """
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)}, _WORKSPACE_PROJECTION)
    if not workspace:
        raise NotFoundException(ErrorMessages.WORKSPACE_NOT_FOUND)
    if user_role != UserRole.SUPER_ADMIN:
        if workspace_id not in user_workspace_ids:
            raise ForbiddenException("No access to this workspace")
    return WorkspaceResponse.model_validate(serialize_doc(workspace))


async def update_workspace(
    db: AsyncIOMotorDatabase,
    workspace_id: str,
    data: dict,
    user_role: str,
    user_workspace_ids: list,
) -> WorkspaceResponse:
    """Update workspace fields (name, description) after verifying access.

    Raises:
        NotFoundException: If the workspace does not exist.
        ForbiddenException: If the user does not have access.
    """
    await get_workspace(db, workspace_id, user_role, user_workspace_ids)
    update = {k: v for k, v in data.items() if v is not None}
    update["updated_at"] = utcnow()
    await db.workspaces.update_one({"_id": ObjectId(workspace_id)}, {"$set": update})
    updated = await db.workspaces.find_one({"_id": ObjectId(workspace_id)}, _WORKSPACE_PROJECTION)
    return WorkspaceResponse.model_validate(serialize_doc(updated))


async def invite_members(
    db: AsyncIOMotorDatabase, workspace_id: str, user_ids: list
) -> WorkspaceResponse:
    """Add admin users to a workspace by appending the workspace ID to their workspace_ids list.

    Raises:
        NotFoundException: If the workspace does not exist.
    """
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException(ErrorMessages.WORKSPACE_NOT_FOUND)

    for uid in user_ids:
        user = await db.users.find_one({"_id": ObjectId(uid), "role": UserRole.ADMIN})
        if not user:
            continue
        if workspace_id in user.get("workspace_ids", []):
            continue
        set_fields: dict = {"updated_at": utcnow()}
        if not user.get("default_workspace_id"):
            set_fields["default_workspace_id"] = workspace_id
        await db.users.update_one(
            {"_id": ObjectId(uid), "workspace_ids": {"$ne": workspace_id}},
            {_PUSH: {"workspace_ids": workspace_id}, "$set": set_fields},
        )

    refreshed = await db.workspaces.find_one({"_id": ObjectId(workspace_id)}, _WORKSPACE_PROJECTION)
    return WorkspaceResponse.model_validate(serialize_doc(refreshed))


async def get_members(db: AsyncIOMotorDatabase, workspace_id: str) -> list[AdminUserResponse]:
    """Return all admin users who have this workspace in their workspace_ids list.

    Raises:
        NotFoundException: If the workspace does not exist.
    """
    if not await db.workspaces.find_one({"_id": ObjectId(workspace_id)}):
        raise NotFoundException(ErrorMessages.WORKSPACE_NOT_FOUND)

    docs = await db.users.find({"workspace_ids": workspace_id}, _MEMBER_PROJECTION).to_list(200)
    return [AdminUserResponse.model_validate(serialize_doc(d)) for d in docs]


async def delete_workspace(db: AsyncIOMotorDatabase, workspace_id: str) -> None:
    """Delete a workspace and remove its ID from all affected users' workspace_ids.

    Raises:
        NotFoundException: If the workspace does not exist.
    """
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException(ErrorMessages.WORKSPACE_NOT_FOUND)

    affected_users = await db.users.find(
        {"workspace_ids": workspace_id},
        {"_id": 1, "workspace_ids": 1, "default_workspace_id": 1},
    ).to_list(500)

    for user in affected_users:
        remaining = [wid for wid in user.get("workspace_ids", []) if wid != workspace_id]
        update: dict = {"workspace_ids": remaining, "updated_at": utcnow()}
        if user.get("default_workspace_id") == workspace_id:
            update["default_workspace_id"] = remaining[0] if remaining else None
        await db.users.update_one({"_id": user["_id"]}, {"$set": update})

    await db.workspaces.delete_one({"_id": ObjectId(workspace_id)})


async def get_all_admin_users(db: AsyncIOMotorDatabase) -> list[AdminUserResponse]:
    """Return all users with the admin role, used for workspace invite dropdowns."""
    docs = await db.users.find({"role": UserRole.ADMIN}, _MEMBER_PROJECTION).to_list(200)
    return [AdminUserResponse.model_validate(serialize_doc(d)) for d in docs]
