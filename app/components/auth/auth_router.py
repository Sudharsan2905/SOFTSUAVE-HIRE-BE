from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.dependencies import get_db
from app.components.auth import auth_service
from app.components.auth.auth_schemas import (
    SetupRequest,
    AdminLoginRequest,
    CandidateLoginRequest,
    CandidateRegisterRequest,
    RefreshTokenRequest,
)
from app.components.auth.auth_dependencies import get_current_user
from app.common.responses import success_response
from app.common.utils import serialize_doc

router = APIRouter()


@router.post("/setup")
async def setup(request: SetupRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await auth_service.setup_super_admin(db, request.model_dump())
    return success_response("Super admin created successfully", result)


@router.post("/admin/login")
async def admin_login(request: AdminLoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await auth_service.admin_login(db, request.email, request.password)
    return success_response("Login successful", result)


@router.post("/login")
async def candidate_login(
    request: CandidateLoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)
):
    result = await auth_service.candidate_login(db, request.email, request.password)
    return success_response("Login successful", result)


@router.post("/register")
async def register_candidate(
    request: CandidateRegisterRequest, db: AsyncIOMotorDatabase = Depends(get_db)
):
    result = await auth_service.register_candidate(db, request.model_dump())
    return success_response("Registration successful", result)


@router.post("/refresh")
async def refresh_token(
    request: RefreshTokenRequest, db: AsyncIOMotorDatabase = Depends(get_db)
):
    result = await auth_service.refresh_access_token(db, request.refresh_token)
    return success_response("Token refreshed", result)


@router.post("/logout")
async def logout(request: RefreshTokenRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    await auth_service.logout(db, request.refresh_token)
    return success_response("Logged out successfully")


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return success_response("User retrieved", serialize_doc(current_user))
