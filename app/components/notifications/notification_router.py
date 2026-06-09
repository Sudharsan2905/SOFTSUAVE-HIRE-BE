from typing import Annotated

from fastapi import Query

from app.common.constants.messages import SuccessMessages
from app.common.response_models.notification_responses import (
    MarkAllReadResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from app.common.responses import ApiResponse, success_response
from app.common.router import DefaultResponseRouter
from app.components.auth.auth_dependencies import AdminUser
from app.components.notifications import notification_service
from app.components.notifications.notification_schemas import CreateNotificationRequest
from app.core.dependencies import DB

router = DefaultResponseRouter()


@router.get("/unread-count", response_model=ApiResponse[UnreadCountResponse])
async def get_unread_count(db: DB, current_user: AdminUser) -> dict:
    result = await notification_service.get_unread_count(db, current_user["_id"])
    return success_response(SuccessMessages.UNREAD_COUNT_RETRIEVED, result)


@router.get("")
async def list_notifications(
    db: DB,
    current_user: AdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    result = await notification_service.list_notifications(db, current_user["_id"], page, page_size)
    return success_response(SuccessMessages.NOTIFICATIONS_RETRIEVED, result)


@router.post("", response_model=ApiResponse[NotificationResponse])
async def create_notification(
    request: CreateNotificationRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await notification_service.create_notification(
        db,
        current_user["_id"],
        request.type,
        request.title,
        request.message,
        request.link,
    )
    return success_response(SuccessMessages.NOTIFICATION_CREATED, result)


@router.patch("/mark-all-read", response_model=ApiResponse[MarkAllReadResponse])
async def mark_all_as_read(db: DB, current_user: AdminUser) -> dict:
    result = await notification_service.mark_all_as_read(db, current_user["_id"])
    return success_response(SuccessMessages.ALL_NOTIFICATIONS_READ, result)


@router.patch("/{notification_id}/read", response_model=ApiResponse[NotificationResponse])
async def mark_as_read(notification_id: str, db: DB, current_user: AdminUser) -> dict:
    result = await notification_service.mark_as_read(db, notification_id, current_user["_id"])
    return success_response(SuccessMessages.NOTIFICATION_READ, result)


@router.delete("/{notification_id}")
async def delete_notification(notification_id: str, db: DB, current_user: AdminUser) -> dict:
    await notification_service.delete_notification(db, notification_id, current_user["_id"])
    return success_response(SuccessMessages.NOTIFICATION_DELETED)
