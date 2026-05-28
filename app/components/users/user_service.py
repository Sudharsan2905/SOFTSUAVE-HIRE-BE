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
    workspace_ids = data.get("workspace_ids") or []
    workspaces = []

    if role != "super_admin":
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
        "workspaces": ws_refs,
        "default_workspace_id": first_ws_id,
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

        all_workspaces = await db.workspaces.find().to_list(500)
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

        update["workspaces"] = [
            {"id": wid, "name": ws_map[wid]["name"]}
            for wid in workspace_ids if wid in ws_map
        ]

        # Update default_workspace_id if the current default was removed
        current_default = user.get("default_workspace_id")
        if current_default not in workspace_ids:
            update["default_workspace_id"] = workspace_ids[0] if workspace_ids else None

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

    if data.get("default_workspace_id"):
        workspace_id = data["default_workspace_id"]
        workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
        if not workspace:
            raise NotFoundException("Workspace not found")
        member_ids = [str(m["user_id"]) for m in workspace.get("members", [])]
        if user_id not in member_ids:
            raise ForbiddenException("You are not a member of this workspace")
        update["default_workspace_id"] = workspace_id

    if update:
        update["updated_at"] = utcnow()
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))
