from fastapi import Request

from app.common.constants.messages import SuccessMessages
from app.common.response_models.auth_responses import (
    AuthTokenResponse,
    GooglePreAuthResponse,
    TokenRefreshResponse,
)
from app.common.responses import ApiResponse, success_response
from app.common.router import DefaultResponseRouter
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

router = DefaultResponseRouter()


@router.post("/setup", response_model=ApiResponse[AuthTokenResponse])
@limiter.limit("3/hour")
async def setup(request: Request, body: SetupRequest, db: DB) -> dict:
    result = await auth_service.setup_super_admin(db, body.model_dump())
    return success_response(SuccessMessages.SETUP_COMPLETE, result)


@router.post("/admin/login", response_model=ApiResponse[AuthTokenResponse])
@limiter.limit("10/minute")
async def admin_login(request: Request, body: AdminLoginRequest, db: DB) -> dict:
    result = await auth_service.admin_login(db, body.email, body.password)
    return success_response(SuccessMessages.LOGIN_SUCCESS, result)


@router.post("/login", response_model=ApiResponse[AuthTokenResponse])
@limiter.limit("10/minute")
async def candidate_login(request: Request, body: CandidateLoginRequest, db: DB) -> dict:
    result = await auth_service.candidate_login(db, body.email, body.password, body.share_link)
    return success_response(SuccessMessages.LOGIN_SUCCESS, result)


@router.post("/register", response_model=ApiResponse[AuthTokenResponse])
@limiter.limit("5/minute")
async def register_candidate(request: Request, body: CandidateRegisterRequest, db: DB) -> dict:
    result = await auth_service.register_candidate(db, body.model_dump())
    return success_response(SuccessMessages.CANDIDATE_CREATED, result)


@router.post("/google", response_model=ApiResponse[AuthTokenResponse | GooglePreAuthResponse])
@limiter.limit("10/minute")
async def google_login(request: Request, body: GoogleAuthRequest, db: DB) -> dict:
    result = await auth_service.google_auth(db, body.credential)
    return success_response(SuccessMessages.LOGIN_SUCCESS, result)


@router.post("/refresh", response_model=ApiResponse[TokenRefreshResponse])
@limiter.limit("20/minute")
async def refresh_token(request: Request, body: RefreshTokenRequest, db: DB) -> dict:
    result = await auth_service.refresh_access_token(db, body.refresh_token)
    return success_response(SuccessMessages.TOKEN_REFRESHED, result)


@router.post("/logout")
@limiter.limit("20/minute")
async def logout(request: Request, body: RefreshTokenRequest, db: DB) -> dict:
    await auth_service.logout(db, body.refresh_token)
    return success_response(SuccessMessages.LOGOUT_SUCCESS)


@router.get("/me")
async def get_me(current_user: CurrentUser) -> dict:
    return success_response(SuccessMessages.USER_RETRIEVED, serialize_doc(current_user))
