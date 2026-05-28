from pydantic import BaseModel, EmailStr, Field

from app.common.constants.app_constants import UserRole


class CreateAdminUserRequest(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.ADMIN
    workspace_ids: list[str] | None = None


class UpdateUserRequest(BaseModel):
    first_name: str | None = Field(None, min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    is_active: bool | None = None
    workspace_ids: list[str] | None = None
    default_workspace_id: str | None = None


class UpdateMeRequest(BaseModel):
    first_name: str | None = Field(None, min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    password: str | None = Field(None, min_length=8)
    default_workspace_id: str | None = None
