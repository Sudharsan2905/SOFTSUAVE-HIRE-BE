from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import UserRole
from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.utils import (
    build_pagination_meta,
    list_paginated,
    paginate_query,
    serialize_doc,
    serialize_docs,
    utcnow,
)
from app.core.logging import logger

_ERR_WS_NOT_FOUND = "Workspace not found"
_PUSH = "$push"
_WS_ID_FIELD = "workspaces.id"


async def create_workspace(db: AsyncIOMotorDatabase, data: dict, created_by: str) -> dict:
    """Create a new workspace and sync the creator's workspaces list.

    Adds the new workspace to the creator's workspaces array and sets it as their
    default_workspace_id if they don't already have one.
    """
    now = utcnow()
    doc = {
        "name": data["name"],
        "description": data.get("description", ""),
        "created_by": ObjectId(created_by),
        "members": [],
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
            # Super admins have implicit access to all workspaces — only set default if unset
            await db.users.update_one({"_id": ObjectId(created_by)}, {"$set": set_fields})
        else:
            ws_ref = {"id": workspace_id, "name": data["name"]}
            await db.users.update_one(
                {"_id": ObjectId(created_by), _WS_ID_FIELD: {"$ne": workspace_id}},
                {_PUSH: {"workspaces": ws_ref}, "$set": set_fields},
            )

    logger.info(f"Workspace created: {data['name']} by user_id={created_by}")
    return serialize_doc(doc)


async def get_workspaces(
    db: AsyncIOMotorDatabase,
    user_id: str,
    user_role: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Return a paginated list of workspaces visible to the requesting user.

    Super admins see all workspaces; admins see only those they are members of.
    """
    skip, limit = paginate_query(page, page_size)
    query = {} if user_role == UserRole.SUPER_ADMIN else {"members.user_id": ObjectId(user_id)}
    total, docs = await list_paginated(
        db.workspaces, query, "created_at", -1, skip, limit, ["created_at"]
    )
    return {
        "workspaces": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }


async def get_workspace(
    db: AsyncIOMotorDatabase, workspace_id: str, user_id: str, user_role: str
) -> dict:
    """Fetch a single workspace, enforcing membership access for non-super_admin users.

    Raises:
        NotFoundException: If the workspace does not exist.
        ForbiddenException: If the user is not a member (admin role only).
    """
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException(_ERR_WS_NOT_FOUND)
    if user_role != UserRole.SUPER_ADMIN:
        member_ids = [str(m["user_id"]) for m in workspace.get("members", [])]
        if user_id not in member_ids:
            raise ForbiddenException("No access to this workspace")
    return serialize_doc(workspace)


async def update_workspace(
    db: AsyncIOMotorDatabase, workspace_id: str, data: dict, user_id: str, user_role: str
) -> dict:
    """Update workspace fields (name, description) after verifying access.

    Raises:
        NotFoundException: If the workspace does not exist.
        ForbiddenException: If the user does not have access.
    """
    await get_workspace(db, workspace_id, user_id, user_role)
    update = {k: v for k, v in data.items() if v is not None}
    update["updated_at"] = utcnow()
    await db.workspaces.update_one({"_id": ObjectId(workspace_id)}, {"$set": update})
    updated = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    return serialize_doc(updated)


async def invite_members(db: AsyncIOMotorDatabase, workspace_id: str, user_ids: list) -> dict:
    """Add admin/super_admin users to a workspace and sync their admin_data.workspaces list.

    Skips users already in the workspace. Sets the workspace as the user's
    default if they have no default set yet.

    Raises:
        NotFoundException: If the workspace does not exist.
    """
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException(_ERR_WS_NOT_FOUND)

    existing_ids = {str(m["user_id"]) for m in workspace.get("members", [])}
    new_members = []
    newly_added_users = []
    newly_added_user_docs: dict = {}

    for uid in user_ids:
        if uid not in existing_ids:
            user = await db.users.find_one({"_id": ObjectId(uid), "role": UserRole.ADMIN})
            if user:
                new_members.append(
                    {"user_id": ObjectId(uid), "email": user["email"], "role": user["role"]}
                )
                newly_added_users.append(uid)
                newly_added_user_docs[uid] = user

    if new_members:
        await db.workspaces.update_one(
            {"_id": ObjectId(workspace_id)},
            {
                _PUSH: {"members": {"$each": new_members}},
                "$set": {"updated_at": utcnow()},
            },
        )
        ws_ref = {"id": workspace_id, "name": workspace["name"]}
        for uid in newly_added_users:
            user_doc = newly_added_user_docs[uid]
            set_fields: dict = {"updated_at": utcnow()}
            if not user_doc.get("default_workspace_id"):
                set_fields["default_workspace_id"] = workspace_id
            await db.users.update_one(
                {"_id": ObjectId(uid), _WS_ID_FIELD: {"$ne": workspace_id}},
                {
                    _PUSH: {"workspaces": ws_ref},
                    "$set": set_fields,
                },
            )

    updated = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    return serialize_doc(updated)


async def get_members(db: AsyncIOMotorDatabase, workspace_id: str) -> list:
    """Return the full user documents for all members of a workspace.

    Raises:
        NotFoundException: If the workspace does not exist.
    """
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException(_ERR_WS_NOT_FOUND)

    member_ids = [m["user_id"] for m in workspace.get("members", [])]
    users = await db.users.find({"_id": {"$in": member_ids}}, {"password_hash": 0}).to_list(200)
    return serialize_docs(users)


async def delete_workspace(db: AsyncIOMotorDatabase, workspace_id: str) -> None:
    """Delete a workspace and clean up all affected users' admin_data workspace references.

    Updates each user's admin_data.workspaces list and resets admin_data.default_workspace_id
    if it pointed to the deleted workspace.

    Raises:
        NotFoundException: If the workspace does not exist.
    """
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException(_ERR_WS_NOT_FOUND)

    affected_users = await db.users.find(
        {_WS_ID_FIELD: workspace_id}, {"_id": 1, "workspaces": 1, "default_workspace_id": 1}
    ).to_list(500)

    for user in affected_users:
        remaining = [w for w in user.get("workspaces", []) if w["id"] != workspace_id]
        update: dict = {
            "workspaces": remaining,
            "updated_at": utcnow(),
        }
        if user.get("default_workspace_id") == workspace_id:
            update["default_workspace_id"] = remaining[0]["id"] if remaining else None

        await db.users.update_one({"_id": user["_id"]}, {"$set": update})

    await db.workspaces.delete_one({"_id": ObjectId(workspace_id)})


async def get_all_admin_users(db: AsyncIOMotorDatabase) -> list:
    """Return all users with the admin role, used for workspace invite dropdowns."""
    users = await db.users.find({"role": UserRole.ADMIN}, {"password_hash": 0}).to_list(200)
    return serialize_docs(users)
