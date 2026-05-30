from typing import Annotated

from fastapi import APIRouter, Query

from app.common.responses import ApiResponse, success_response
from app.components.auth.auth_dependencies import AdminUser, SuperAdminUser
from app.components.workspace import workspace_service
from app.components.workspace.workspace_schemas import (
    CreateWorkspaceRequest,
    InviteMemberRequest,
    UpdateWorkspaceRequest,
)
from app.core.dependencies import DB

router = APIRouter()


@router.get("", response_model=ApiResponse)
async def list_workspaces(
    db: DB,
    current_user: AdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    result = await workspace_service.get_workspaces(
        db, current_user["_id"], current_user["role"], page, page_size
    )
    return success_response("Workspaces retrieved", result)


@router.post("", response_model=ApiResponse)
async def create_workspace(
    request: CreateWorkspaceRequest,
    db: DB,
    current_user: SuperAdminUser,
):
    result = await workspace_service.create_workspace(db, request.model_dump(), current_user["_id"])
    return success_response("Workspace created", result)


@router.get("/admin-users", response_model=ApiResponse)
async def list_admin_users(
    db: DB,
    current_user: SuperAdminUser,
):
    result = await workspace_service.get_all_admin_users(db)
    return success_response("Admin users retrieved", result)


@router.get("/{workspace_id}", response_model=ApiResponse)
async def get_workspace(
    workspace_id: str,
    db: DB,
    current_user: AdminUser,
):
    result = await workspace_service.get_workspace(
        db, workspace_id, current_user["_id"], current_user["role"]
    )
    return success_response("Workspace retrieved", result)


@router.put("/{workspace_id}", response_model=ApiResponse)
async def update_workspace(
    workspace_id: str,
    request: UpdateWorkspaceRequest,
    db: DB,
    current_user: AdminUser,
):
    result = await workspace_service.update_workspace(
        db, workspace_id, request.model_dump(), current_user["_id"], current_user["role"]
    )
    return success_response("Workspace updated", result)


@router.post("/{workspace_id}/invite", response_model=ApiResponse)
async def invite_members(
    workspace_id: str,
    request: InviteMemberRequest,
    db: DB,
    current_user: SuperAdminUser,
):
    result = await workspace_service.invite_members(
        db, workspace_id, request.user_ids, current_user["_id"]
    )
    return success_response("Members invited", result)


@router.delete("/{workspace_id}", response_model=ApiResponse)
async def delete_workspace(
    workspace_id: str,
    db: DB,
    current_user: SuperAdminUser,
):
    await workspace_service.delete_workspace(db, workspace_id)
    return success_response("Workspace deleted", None)


@router.get("/{workspace_id}/members", response_model=ApiResponse)
async def get_members(
    workspace_id: str,
    db: DB,
    current_user: AdminUser,
):
    result = await workspace_service.get_members(db, workspace_id)
    return success_response("Members retrieved", result)
