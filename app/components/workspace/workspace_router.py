from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.dependencies import get_db
from app.components.auth.auth_dependencies import require_admin, require_super_admin
from app.components.workspace import workspace_service
from app.components.workspace.workspace_schemas import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
    InviteMemberRequest,
)
from app.common.responses import success_response

router = APIRouter()


@router.get("")
async def list_workspaces(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await workspace_service.get_workspaces(
        db, current_user["_id"], current_user["role"], page, page_size
    )
    return success_response("Workspaces retrieved", result)


@router.post("")
async def create_workspace(
    request: CreateWorkspaceRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_super_admin),
):
    result = await workspace_service.create_workspace(db, request.model_dump(), current_user["_id"])
    return success_response("Workspace created", result)


@router.get("/admin-users")
async def list_admin_users(
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_super_admin),
):
    result = await workspace_service.get_all_admin_users(db)
    return success_response("Admin users retrieved", result)


@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await workspace_service.get_workspace(
        db, workspace_id, current_user["_id"], current_user["role"]
    )
    return success_response("Workspace retrieved", result)


@router.put("/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    request: UpdateWorkspaceRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await workspace_service.update_workspace(
        db, workspace_id, request.model_dump(), current_user["_id"], current_user["role"]
    )
    return success_response("Workspace updated", result)


@router.post("/{workspace_id}/invite")
async def invite_members(
    workspace_id: str,
    request: InviteMemberRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_super_admin),
):
    result = await workspace_service.invite_members(
        db, workspace_id, request.user_ids, current_user["_id"]
    )
    return success_response("Members invited", result)


@router.get("/{workspace_id}/members")
async def get_members(
    workspace_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await workspace_service.get_members(db, workspace_id)
    return success_response("Members retrieved", result)
