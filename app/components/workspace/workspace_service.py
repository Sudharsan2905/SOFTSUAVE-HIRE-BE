from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.utils import (
    build_pagination_meta,
    paginate_query,
    serialize_doc,
    serialize_docs,
    utcnow,
)


async def create_workspace(db: AsyncIOMotorDatabase, data: dict, created_by: str) -> dict:
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
    doc["_id"] = result.inserted_id
    return serialize_doc(doc)


async def get_workspaces(
    db: AsyncIOMotorDatabase,
    user_id: str,
    user_role: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    skip, limit = paginate_query(page, page_size)
    query = {} if user_role == "super_admin" else {"members.user_id": ObjectId(user_id)}
    total = await db.workspaces.count_documents(query)
    docs = (
        await db.workspaces.find(query)
        .sort([("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {
        "workspaces": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }


async def get_workspace(
    db: AsyncIOMotorDatabase, workspace_id: str, user_id: str, user_role: str
) -> dict:
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException("Workspace not found")
    if user_role != "super_admin":
        member_ids = [str(m["user_id"]) for m in workspace.get("members", [])]
        if user_id not in member_ids:
            raise ForbiddenException("No access to this workspace")
    return serialize_doc(workspace)


async def update_workspace(
    db: AsyncIOMotorDatabase, workspace_id: str, data: dict, user_id: str, user_role: str
) -> dict:
    await get_workspace(db, workspace_id, user_id, user_role)
    update = {k: v for k, v in data.items() if v is not None}
    update["updated_at"] = utcnow()
    await db.workspaces.update_one({"_id": ObjectId(workspace_id)}, {"$set": update})
    updated = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    return serialize_doc(updated)


async def invite_members(
    db: AsyncIOMotorDatabase, workspace_id: str, user_ids: list, current_user_id: str
) -> dict:
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException("Workspace not found")

    existing_ids = {str(m["user_id"]) for m in workspace.get("members", [])}
    new_members = []
    newly_added_users = []
    newly_added_user_docs: dict = {}

    for uid in user_ids:
        if uid not in existing_ids:
            user = await db.users.find_one(
                {"_id": ObjectId(uid), "role": {"$in": ["admin", "super_admin"]}}
            )
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
                "$push": {"members": {"$each": new_members}},
                "$set": {"updated_at": utcnow()},
            },
        )
        # Sync each newly added user's workspaces array and set default if unset
        ws_ref = {"id": workspace_id, "name": workspace["name"]}
        for uid in newly_added_users:
            user_doc = newly_added_user_docs[uid]
            set_fields: dict = {"updated_at": utcnow()}
            if not user_doc.get("default_workspace_id"):
                set_fields["default_workspace_id"] = workspace_id
            await db.users.update_one(
                {"_id": ObjectId(uid), "workspaces.id": {"$ne": workspace_id}},
                {
                    "$push": {"workspaces": ws_ref},
                    "$set": set_fields,
                },
            )

    updated = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    return serialize_doc(updated)


async def get_members(db: AsyncIOMotorDatabase, workspace_id: str) -> list:
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException("Workspace not found")

    member_ids = [m["user_id"] for m in workspace.get("members", [])]
    users = await db.users.find({"_id": {"$in": member_ids}}, {"password_hash": 0}).to_list(200)
    return serialize_docs(users)


async def delete_workspace(db: AsyncIOMotorDatabase, workspace_id: str) -> None:
    workspace = await db.workspaces.find_one({"_id": ObjectId(workspace_id)})
    if not workspace:
        raise NotFoundException("Workspace not found")

    # Find all users who belong to this workspace
    affected_users = await db.users.find(
        {"workspaces.id": workspace_id}, {"_id": 1, "workspaces": 1, "default_workspace_id": 1}
    ).to_list(500)

    for user in affected_users:
        user_id = user["_id"]
        remaining = [w for w in user.get("workspaces", []) if w["id"] != workspace_id]
        update: dict = {"workspaces": remaining, "updated_at": utcnow()}

        current_default = user.get("default_workspace_id")
        if current_default == workspace_id:
            update["default_workspace_id"] = remaining[0]["id"] if remaining else None

        await db.users.update_one({"_id": user_id}, {"$set": update})

    await db.workspaces.delete_one({"_id": ObjectId(workspace_id)})


async def get_all_admin_users(db: AsyncIOMotorDatabase) -> list:
    users = await db.users.find({"role": "admin"}, {"password_hash": 0}).to_list(200)
    return serialize_docs(users)
