from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.responses import success_response
from app.components.auth.auth_dependencies import get_current_user, require_super_admin
from app.components.users import user_service
from app.components.users.user_schemas import (
    CreateAdminUserRequest,
    UpdateMeRequest,
    UpdateUserRequest,
)
from app.core.dependencies import get_db

router = APIRouter()


@router.patch("/me")
async def update_me(
    request: UpdateMeRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await user_service.update_me(
        db, current_user["_id"], request.model_dump(exclude_none=True)
    )
    return success_response("Profile updated", result)


@router.post("")
async def create_user(
    request: CreateAdminUserRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_super_admin),
):
    result = await user_service.create_admin_user(db, request.model_dump())
    return success_response("User created", result)


@router.get("")
async def list_users(
    role: str | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_super_admin),
):
    result = await user_service.list_users(db, role, is_active)
    return success_response("Users retrieved", result)


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_super_admin),
):
    result = await user_service.get_user(db, user_id)
    return success_response("User retrieved", result)


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_super_admin),
):
    result = await user_service.update_user(db, user_id, request.model_dump(exclude_none=True))
    return success_response("User updated", result)


@router.patch("/{user_id}")
async def patch_user(
    user_id: str,
    request: UpdateUserRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_super_admin),
):
    result = await user_service.update_user(db, user_id, request.model_dump(exclude_none=True))
    return success_response("User updated", result)
