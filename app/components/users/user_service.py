from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from typing import Optional
from app.common.exceptions import ConflictException, NotFoundException, ForbiddenException
from app.common.utils import utcnow, serialize_doc, serialize_docs
from app.components.auth.auth_service import hash_password


async def create_admin_user(db: AsyncIOMotorDatabase, data: dict) -> dict:
    if await db.users.find_one({"email": data["email"]}):
        raise ConflictException("Email already registered")

    role = data.get("role", "admin")
    is_super_admin = role == "super_admin"

    # Super admins have global access — workspaces list is always empty
    # Admins are pre-assigned to Common Workspace
    common_ws = None if is_super_admin else await db.workspaces.find_one({"name": "Common"})

    now = utcnow()
    doc = {
        "first_name": data["first_name"],
        "last_name": data.get("last_name") or "",
        "email": data["email"],
        "password_hash": hash_password(data["password"]),
        "role": role,
        "is_active": True,
        "workspaces": (
            [{"id": str(common_ws["_id"]), "name": common_ws["name"], "is_default": True}]
            if common_ws else []
        ),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(doc)
    user_id = result.inserted_id

    # Add admin to Common Workspace members (super admins are never added)
    if common_ws:
        existing_ids = {str(m["user_id"]) for m in common_ws.get("members", [])}
        if str(user_id) not in existing_ids:
            await db.workspaces.update_one(
                {"_id": common_ws["_id"]},
                {
                    "$push": {"members": {"user_id": user_id, "email": data["email"], "role": role}},
                    "$set": {"updated_at": utcnow()},
                },
            )

    doc["_id"] = user_id
    doc.pop("password_hash")
    return serialize_doc(doc)


async def list_users(
    db: AsyncIOMotorDatabase,
    role_filter: Optional[str] = None,
    is_active_filter: Optional[bool] = None,
) -> list:
    query: dict = {"role": {"$in": ["admin", "super_admin"]}}
    if role_filter:
        query["role"] = role_filter
    if is_active_filter is not None:
        query["is_active"] = is_active_filter
    docs = await db.users.find(query, {"password_hash": 0}).to_list(500)
    return serialize_docs(docs)


async def get_user(db: AsyncIOMotorDatabase, user_id: str) -> dict:
    doc = await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0})
    if not doc:
        raise NotFoundException("User not found")
    return serialize_doc(doc)


async def update_user(db: AsyncIOMotorDatabase, user_id: str, data: dict) -> dict:
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise NotFoundException("User not found")

    update: dict = {}

    if data.get("first_name") is not None:
        update["first_name"] = data["first_name"]
    if data.get("last_name") is not None:
        update["last_name"] = data["last_name"]

    if data.get("is_active") is not None:
        if user["role"] == "super_admin":
            raise ForbiddenException("Super admin status cannot be changed")
        update["is_active"] = data["is_active"]

    if data.get("workspace_ids") is not None:
        if user["role"] == "super_admin":
            raise ForbiddenException("Super admin has access to all workspaces")
        workspace_ids: list = data["workspace_ids"]

        # Sync workspace.members so WorkspaceSwitcher stays accurate (exclude Common Workspace — auto-assigned)
        all_workspaces = await db.workspaces.find({"name": {"$ne": "Common"}}).to_list(500)
        ws_map = {str(ws["_id"]): ws for ws in all_workspaces}
        for ws in all_workspaces:
            ws_id_str = str(ws["_id"])
            member_ids = {str(m["user_id"]) for m in ws.get("members", [])}
            should_be_member = ws_id_str in workspace_ids
            is_member = user_id in member_ids

            if should_be_member and not is_member:
                await db.workspaces.update_one(
                    {"_id": ws["_id"]},
                    {
                        "$push": {"members": {"user_id": ObjectId(user_id), "email": user["email"], "role": user["role"]}},
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

        # Store workspace objects preserving each workspace's is_default flag
        current_ws_map = {w["id"]: w for w in user.get("workspaces", [])}

        assigned = [
            {
                "id": wid,
                "name": ws_map[wid]["name"],
                "is_default": current_ws_map.get(wid, {}).get("is_default", False),
            }
            for wid in workspace_ids if wid in ws_map
        ]

        common_ws = await db.workspaces.find_one({"name": "Common"})
        if common_ws:
            common_id = str(common_ws["_id"])
            common_ref = [{
                "id": common_id,
                "name": "Common",
                "is_default": current_ws_map.get(common_id, {}).get("is_default", True),
            }]
        else:
            common_ref = []

        all_ws = common_ref + assigned
        # If the previous default was removed, fall back to Common Workspace
        if not any(w["is_default"] for w in all_ws) and common_ref:
            common_ref[0]["is_default"] = True

        update["workspaces"] = all_ws

    if update:
        update["updated_at"] = utcnow()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))


async def update_me(db: AsyncIOMotorDatabase, user_id: str, data: dict) -> dict:
    user = await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0})
    if not user:
        raise NotFoundException("User not found")

    update: dict = {}

    if data.get("first_name"):
        update["first_name"] = data["first_name"]
    if data.get("last_name") is not None:
        update["last_name"] = data["last_name"]

    if data.get("password"):
        update["password_hash"] = hash_password(data["password"])

    if data.get("workspace_id"):
        workspace_id = data["workspace_id"]
        workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
        if not workspace:
            raise NotFoundException("Workspace not found")
        member_ids = [str(m["user_id"]) for m in workspace.get("members", [])]
        if user_id not in member_ids:
            raise ForbiddenException("You are not a member of this workspace")
        workspaces = user.get("workspaces", [])
        # Ensure the workspace is in user.workspaces (may be missing if added via invite)
        if not any(w.get("id") == workspace_id for w in workspaces):
            workspaces = workspaces + [{"id": workspace_id, "name": workspace["name"], "is_default": False}]
        update["workspaces"] = [{**w, "is_default": w.get("id") == workspace_id} for w in workspaces]

    if update:
        update["updated_at"] = utcnow()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))
