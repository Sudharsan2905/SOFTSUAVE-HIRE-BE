from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from typing import Optional
from app.common.exceptions import ConflictException, NotFoundException
from app.common.utils import utcnow, serialize_doc, serialize_docs
from app.components.auth.auth_service import hash_password


async def create_admin_user(db: AsyncIOMotorDatabase, data: dict) -> dict:
    if await db.users.find_one({"email": data["email"]}):
        raise ConflictException("Email already registered")

    now = utcnow()
    doc = {
        "name": data["name"],
        "email": data["email"],
        "password_hash": hash_password(data["password"]),
        "role": data["role"],
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    doc.pop("password_hash")
    return serialize_doc(doc)


async def list_users(db: AsyncIOMotorDatabase, role_filter: Optional[str] = None) -> list:
    query: dict = {"role": {"$in": ["admin", "super_admin"]}}
    if role_filter:
        query["role"] = role_filter
    docs = await db.users.find(query, {"password_hash": 0}).to_list(500)
    return serialize_docs(docs)


async def get_user(db: AsyncIOMotorDatabase, user_id: str) -> dict:
    doc = await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0})
    if not doc:
        raise NotFoundException("User not found")
    return serialize_doc(doc)


async def update_user(db: AsyncIOMotorDatabase, user_id: str, data: dict) -> dict:
    if not await db.users.find_one({"_id": ObjectId(user_id)}):
        raise NotFoundException("User not found")
    update = {k: v for k, v in data.items() if v is not None}
    update["updated_at"] = utcnow()
    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})
    return serialize_doc(await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0}))
