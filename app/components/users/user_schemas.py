from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from app.common.constants.app_constants import UserRole


class CreateAdminUserRequest(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: Optional[str] = Field(None, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.ADMIN


class UpdateUserRequest(BaseModel):
    first_name: Optional[str] = Field(None, min_length=2, max_length=50)
    last_name: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    workspace_ids: Optional[List[str]] = None


class UpdateMeRequest(BaseModel):
    first_name: Optional[str] = Field(None, min_length=2, max_length=50)
    last_name: Optional[str] = Field(None, max_length=50)
    password: Optional[str] = Field(None, min_length=8)
    workspace_id: Optional[str] = None  # sets this workspace as the default
