from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING

from app.common.exceptions import NotFoundException
from app.common.utils import (
    build_pagination_meta,
    generate_uuid,
    paginate_query,
    serialize_doc,
    serialize_docs,
)


async def list_notifications(
    db: AsyncIOMotorDatabase,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    skip, limit = paginate_query(page, page_size)
    query = {"user_id": user_id}

    total = await db.notifications.count_documents(query)
    docs = (
        await db.notifications.find(query)
        .sort("created_at", DESCENDING)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    unread_count = await db.notifications.count_documents({**query, "is_read": False})

    return {
        "notifications": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
        "unread_count": unread_count,
    }


async def get_unread_count(db: AsyncIOMotorDatabase, user_id: str) -> int:
    return await db.notifications.count_documents({"user_id": user_id, "is_read": False})


async def mark_as_read(
    db: AsyncIOMotorDatabase,
    notification_id: str,
    user_id: str,
) -> dict:
    doc = await db.notifications.find_one_and_update(
        {"_id": notification_id, "user_id": user_id},
        {"$set": {"is_read": True, "read_at": datetime.now(UTC)}},
        return_document=True,
    )
    if not doc:
        raise NotFoundException("Notification not found")
    return serialize_doc(doc)


async def mark_all_as_read(db: AsyncIOMotorDatabase, user_id: str) -> dict:
    result = await db.notifications.update_many(
        {"user_id": user_id, "is_read": False},
        {"$set": {"is_read": True, "read_at": datetime.now(UTC)}},
    )
    return {"modified": result.modified_count}


async def create_notification(
    db: AsyncIOMotorDatabase,
    user_id: str,
    type_: str,
    title: str,
    message: str,
    link: str | None = None,
) -> dict:
    doc = {
        "_id": generate_uuid(),
        "user_id": user_id,
        "type": type_,
        "title": title,
        "message": message,
        "link": link,
        "is_read": False,
        "read_at": None,
        "created_at": datetime.now(UTC),
    }
    await db.notifications.insert_one(doc)
    return serialize_doc(doc)


async def delete_notification(
    db: AsyncIOMotorDatabase,
    notification_id: str,
    user_id: str,
) -> None:
    result = await db.notifications.delete_one({"_id": notification_id, "user_id": user_id})
    if result.deleted_count == 0:
        raise NotFoundException("Notification not found")
