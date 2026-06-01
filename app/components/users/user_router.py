from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.common.responses import ApiResponse, success_response
from app.components.auth.auth_dependencies import AdminUser, CurrentUser, SuperAdminUser
from app.components.users import user_service
from app.components.users.user_schemas import (
    CreateAdminUserRequest,
    CreateCandidateAdminRequest,
    UpdateCandidateRequest,
    UpdateMeRequest,
    UpdateUserRequest,
)
from app.core.dependencies import DB
from app.core.limiter import limiter

router = APIRouter()


# ── Self ──────────────────────────────────────────────────────────────────────


@router.patch("/me", response_model=ApiResponse)
async def update_me(
    request: UpdateMeRequest,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await user_service.update_me(
        db, current_user["_id"], request.model_dump(exclude_none=True)
    )
    return success_response("Profile updated", result)


# ── Candidates (fixed paths — must be before /{user_id}) ─────────────────────


@router.get("/candidates/search", response_model=ApiResponse)
async def search_candidate_by_email(
    db: DB,
    current_user: AdminUser,
    email: Annotated[str, Query(min_length=1)],
) -> dict:
    """Find a single candidate by exact email. Used by the Schedule Wizard."""
    result = await user_service.search_candidates_by_email(db, email)
    return success_response("Candidate search result", {"user": result})


@router.post("/candidates", response_model=ApiResponse)
@limiter.limit("30/hour")
async def create_candidate(
    request: Request,
    body: CreateCandidateAdminRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    """Create a new candidate account from admin (Schedule Wizard onboarding)."""
    result = await user_service.create_candidate_from_admin(db, body.model_dump())
    return success_response("Candidate created", result)


@router.get("/candidates", response_model=ApiResponse)
async def list_candidates(
    db: DB,
    current_user: AdminUser,
    search: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    candidate_type: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    result = await user_service.list_candidates(
        db, search, is_active, candidate_type, page, page_size
    )
    return success_response("Candidates retrieved", result)


@router.get("/candidates/{user_id}", response_model=ApiResponse)
async def get_candidate(
    user_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await user_service.get_candidate(db, user_id)
    return success_response("Candidate retrieved", result)


@router.put("/candidates/{user_id}", response_model=ApiResponse)
async def update_candidate(
    user_id: str,
    request: UpdateCandidateRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await user_service.update_candidate(db, user_id, request.model_dump(exclude_none=True))
    return success_response("Candidate updated", result)


@router.patch("/candidates/{user_id}", response_model=ApiResponse)
async def patch_candidate(
    user_id: str,
    request: UpdateCandidateRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await user_service.update_candidate(db, user_id, request.model_dump(exclude_none=True))
    return success_response("Candidate updated", result)


# ── Admins / Super admins (parameterised — must be after fixed paths) ─────────


@router.post("", response_model=ApiResponse)
@limiter.limit("10/hour")
async def create_user(
    request: Request,
    body: CreateAdminUserRequest,
    db: DB,
    current_user: SuperAdminUser,
) -> dict:
    result = await user_service.create_admin_user(db, body.model_dump())
    return success_response("User created", result)


@router.get("", response_model=ApiResponse)
async def list_users(
    db: DB,
    current_user: SuperAdminUser,
    role: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> dict:
    result = await user_service.list_users(db, current_user["_id"], role, is_active)
    return success_response("Users retrieved", result)


@router.get("/{user_id}", response_model=ApiResponse)
async def get_user(
    user_id: str,
    db: DB,
    current_user: SuperAdminUser,
) -> dict:
    result = await user_service.get_user(db, user_id)
    return success_response("User retrieved", result)


@router.put("/{user_id}", response_model=ApiResponse)
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    db: DB,
    current_user: SuperAdminUser,
) -> dict:
    result = await user_service.update_user(db, user_id, request.model_dump(exclude_none=True))
    return success_response("User updated", result)


@router.patch("/{user_id}", response_model=ApiResponse)
async def patch_user(
    user_id: str,
    request: UpdateUserRequest,
    db: DB,
    current_user: SuperAdminUser,
) -> dict:
    result = await user_service.update_user(db, user_id, request.model_dump(exclude_none=True))
    return success_response("User updated", result)
