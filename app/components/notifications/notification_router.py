from typing import Annotated

from fastapi import APIRouter, Query

from app.common.responses import ApiResponse, success_response
from app.components.auth.auth_dependencies import AdminUser
from app.components.notifications import notification_service
from app.components.notifications.notification_schemas import CreateNotificationRequest
from app.core.dependencies import DB

router = APIRouter()


@router.get("/unread-count", response_model=ApiResponse)
async def get_unread_count(db: DB, current_user: AdminUser) -> dict:
    """Return the number of unread notifications for the current user."""
    count = await notification_service.get_unread_count(db, current_user["_id"])
    return success_response("Unread count retrieved", {"count": count})


@router.get("", response_model=ApiResponse)
async def list_notifications(
    db: DB,
    current_user: AdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Return a paginated list of notifications for the current user."""
    result = await notification_service.list_notifications(db, current_user["_id"], page, page_size)
    return success_response("Notifications retrieved", result)


@router.post("", response_model=ApiResponse)
async def create_notification(
    request: CreateNotificationRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    """Create a notification for the current user (admin self-create)."""
    result = await notification_service.create_notification(
        db,
        current_user["_id"],
        request.type,
        request.title,
        request.message,
        request.link,
    )
    return success_response("Notification created", result)


# NOTE: specific routes before parameterised routes to avoid ambiguity
@router.patch("/mark-all-read", response_model=ApiResponse)
async def mark_all_as_read(db: DB, current_user: AdminUser) -> dict:
    """Mark every unread notification as read for the current user."""
    result = await notification_service.mark_all_as_read(db, current_user["_id"])
    return success_response("All notifications marked as read", result)


@router.patch("/{notification_id}/read", response_model=ApiResponse)
async def mark_as_read(notification_id: str, db: DB, current_user: AdminUser) -> dict:
    """Mark a single notification as read."""
    result = await notification_service.mark_as_read(db, notification_id, current_user["_id"])
    return success_response("Notification marked as read", result)


@router.delete("/{notification_id}", response_model=ApiResponse)
async def delete_notification(notification_id: str, db: DB, current_user: AdminUser) -> dict:
    """Delete a single notification."""
    await notification_service.delete_notification(db, notification_id, current_user["_id"])
    return success_response("Notification deleted", None)
