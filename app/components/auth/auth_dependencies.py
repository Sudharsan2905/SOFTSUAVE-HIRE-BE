from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.dependencies import get_db
from app.components.auth.auth_service import decode_access_token
from app.common.constants.app_constants import UserRole, ADMIN_ROLES
from app.common.exceptions import UnauthorizedException, ForbiddenException
from bson import ObjectId

_security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedException("Invalid token payload")

    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise UnauthorizedException("User not found")

    user["_id"] = str(user["_id"])
    user.pop("password_hash", None)
    return user


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") not in [r.value for r in ADMIN_ROLES]:
        raise ForbiddenException("Admin access required")
    return current_user


async def require_super_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != UserRole.SUPER_ADMIN.value:
        raise ForbiddenException("Super admin access required")
    return current_user


async def require_candidate(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != UserRole.CANDIDATE.value:
        raise ForbiddenException("Candidate access required")
    return current_user
