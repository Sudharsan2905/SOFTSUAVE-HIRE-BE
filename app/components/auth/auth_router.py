from fastapi import APIRouter, Request

from app.common.responses import ApiResponse, success_response
from app.common.utils import serialize_doc
from app.components.auth import auth_service
from app.components.auth.auth_dependencies import CurrentUser
from app.components.auth.auth_schemas import (
    AdminLoginRequest,
    CandidateLoginRequest,
    CandidateRegisterRequest,
    GoogleAuthRequest,
    RefreshTokenRequest,
    SetupRequest,
)
from app.core.dependencies import DB
from app.core.limiter import limiter

router = APIRouter()


@router.post("/setup", response_model=ApiResponse)
@limiter.limit("3/hour")
async def setup(request: Request, body: SetupRequest, db: DB):
    result = await auth_service.setup_super_admin(db, body.model_dump())
    return success_response("Super admin created successfully", result)


@router.post("/admin/login", response_model=ApiResponse)
@limiter.limit("10/minute")
async def admin_login(request: Request, body: AdminLoginRequest, db: DB):
    result = await auth_service.admin_login(db, body.email, body.password)
    return success_response("Login successful", result)


@router.post("/login", response_model=ApiResponse)
@limiter.limit("10/minute")
async def candidate_login(request: Request, body: CandidateLoginRequest, db: DB):
    result = await auth_service.candidate_login(db, body.email, body.password)
    return success_response("Login successful", result)


@router.post("/register", response_model=ApiResponse)
@limiter.limit("5/minute")
async def register_candidate(request: Request, body: CandidateRegisterRequest, db: DB):
    result = await auth_service.register_candidate(db, body.model_dump())
    return success_response("Registration successful", result)


@router.post("/google", response_model=ApiResponse)
@limiter.limit("10/minute")
async def google_login(request: Request, body: GoogleAuthRequest, db: DB):
    result = await auth_service.google_auth(db, body.credential)
    return success_response("Google login successful", result)


@router.post("/refresh", response_model=ApiResponse)
async def refresh_token(request: RefreshTokenRequest, db: DB):
    result = await auth_service.refresh_access_token(db, request.refresh_token)
    return success_response("Token refreshed", result)


@router.post("/logout", response_model=ApiResponse)
async def logout(request: RefreshTokenRequest, db: DB):
    await auth_service.logout(db, request.refresh_token)
    return success_response("Logged out successfully")


@router.get("/me", response_model=ApiResponse)
async def get_me(current_user: CurrentUser):
    return success_response("User retrieved", serialize_doc(current_user))
