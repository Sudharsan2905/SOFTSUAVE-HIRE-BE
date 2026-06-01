from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.common.responses import ApiResponse, success_response
from app.components.auth.auth_dependencies import CurrentUser, SuperAdminUser
from app.components.users import user_service
from app.components.users.user_schemas import (
    CreateAdminUserRequest,
    UpdateMeRequest,
    UpdateUserRequest,
)
from app.core.dependencies import DB
from app.core.limiter import limiter

router = APIRouter()


@router.patch("/me", response_model=ApiResponse)
async def update_me(
    request: UpdateMeRequest,
    db: DB,
    current_user: CurrentUser,
):
    result = await user_service.update_me(
        db, current_user["_id"], request.model_dump(exclude_none=True)
    )
    return success_response("Profile updated", result)


@router.post("", response_model=ApiResponse)
@limiter.limit("10/hour")
async def create_user(
    request: Request,
    body: CreateAdminUserRequest,
    db: DB,
    current_user: SuperAdminUser,
):
    result = await user_service.create_admin_user(db, body.model_dump())
    return success_response("User created", result)


@router.get("", response_model=ApiResponse)
async def list_users(
    db: DB,
    current_user: SuperAdminUser,
    role: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
):
    result = await user_service.list_users(db, current_user["_id"], role, is_active)
    return success_response("Users retrieved", result)


@router.get("/{user_id}", response_model=ApiResponse)
async def get_user(
    user_id: str,
    db: DB,
    current_user: SuperAdminUser,
):
    result = await user_service.get_user(db, user_id)
    return success_response("User retrieved", result)


@router.put("/{user_id}", response_model=ApiResponse)
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    db: DB,
    current_user: SuperAdminUser,
):
    result = await user_service.update_user(db, user_id, request.model_dump(exclude_none=True))
    return success_response("User updated", result)


@router.patch("/{user_id}", response_model=ApiResponse)
async def patch_user(
    user_id: str,
    request: UpdateUserRequest,
    db: DB,
    current_user: SuperAdminUser,
):
    result = await user_service.update_user(db, user_id, request.model_dump(exclude_none=True))
    return success_response("User updated", result)
