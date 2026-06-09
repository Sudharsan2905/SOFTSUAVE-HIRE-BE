from typing import Annotated

from fastapi import Query, Request

from app.common.constants.messages import SuccessMessages
from app.common.response_models.user_responses import AdminUserResponse
from app.common.response_models.workspace_responses import WorkspaceResponse
from app.common.responses import ApiResponse, success_response
from app.common.router import DefaultResponseRouter
from app.components.auth.auth_dependencies import AdminUser, SuperAdminUser
from app.components.workspace import workspace_service
from app.components.workspace.workspace_schemas import (
    CreateWorkspaceRequest,
    InviteMemberRequest,
    UpdateWorkspaceRequest,
)
from app.core.dependencies import DB
from app.core.limiter import limiter

router = DefaultResponseRouter()


@router.get("")
async def list_workspaces(
    db: DB,
    current_user: AdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    result = await workspace_service.get_workspaces(
        db, current_user["role"], current_user.get("workspace_ids", []), page, page_size
    )
    return success_response(SuccessMessages.WORKSPACES_RETRIEVED, result)


@router.post("", response_model=ApiResponse[WorkspaceResponse])
@limiter.limit("10/hour")
async def create_workspace(
    request: Request,
    body: CreateWorkspaceRequest,
    db: DB,
    current_user: SuperAdminUser,
) -> dict:
    result = await workspace_service.create_workspace(db, body.model_dump(), current_user["_id"])
    return success_response(SuccessMessages.WORKSPACE_CREATED, result)


@router.get("/admin-users", response_model=ApiResponse[list[AdminUserResponse]])
async def list_admin_users(
    db: DB,
    current_user: SuperAdminUser,
) -> dict:
    result = await workspace_service.get_all_admin_users(db)
    return success_response(SuccessMessages.ADMIN_USERS_RETRIEVED, result)


@router.get("/{workspace_id}", response_model=ApiResponse[WorkspaceResponse])
async def get_workspace(
    workspace_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await workspace_service.get_workspace(
        db, workspace_id, current_user["role"], current_user.get("workspace_ids", [])
    )
    return success_response(SuccessMessages.WORKSPACE_RETRIEVED, result)


@router.put("/{workspace_id}", response_model=ApiResponse[WorkspaceResponse])
async def update_workspace(
    workspace_id: str,
    request: UpdateWorkspaceRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await workspace_service.update_workspace(
        db,
        workspace_id,
        request.model_dump(),
        current_user["role"],
        current_user.get("workspace_ids", []),
    )
    return success_response(SuccessMessages.WORKSPACE_UPDATED, result)


@router.post("/{workspace_id}/invite", response_model=ApiResponse[WorkspaceResponse])
@limiter.limit("10/hour")
async def invite_members(
    request: Request,
    workspace_id: str,
    body: InviteMemberRequest,
    db: DB,
    current_user: SuperAdminUser,
) -> dict:
    result = await workspace_service.invite_members(db, workspace_id, body.user_ids)
    return success_response(SuccessMessages.MEMBERS_INVITED, result)


@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    db: DB,
    current_user: SuperAdminUser,
) -> dict:
    await workspace_service.delete_workspace(db, workspace_id)
    return success_response(SuccessMessages.WORKSPACE_DELETED)


@router.get("/{workspace_id}/members", response_model=ApiResponse[list[AdminUserResponse]])
async def get_members(
    workspace_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await workspace_service.get_members(db, workspace_id)
    return success_response(SuccessMessages.MEMBERS_RETRIEVED, result)
