from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from app.common.constants.app_constants import UserRole


class CreateAdminUserRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.ADMIN


class UpdateUserRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    is_active: Optional[bool] = None
    workspace_ids: Optional[List[str]] = None


class UpdateMeRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    password: Optional[str] = Field(None, min_length=8)
    workspace_id: Optional[str] = None  # sets this workspace as the default
