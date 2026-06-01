from pydantic import BaseModel, EmailStr, Field, field_validator

from app.common.constants.app_constants import CandidateType, UserRole
from app.common.validators import check_password_strength


class CreateAdminUserRequest(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.ADMIN
    workspace_ids: list[str] | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return check_password_strength(v)


class UpdateUserRequest(BaseModel):
    """Super admin update of any user. All fields are optional."""

    first_name: str | None = Field(None, min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr | None = None
    role: UserRole | None = None
    password: str | None = Field(None, min_length=8)
    is_active: bool | None = None
    workspace_ids: list[str] | None = None
    default_workspace_id: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str | None) -> str | None:
        if v is not None:
            return check_password_strength(v)
        return v


class CandidateDataUpdateRequest(BaseModel):
    """Candidate-specific profile fields updatable via PATCH /users/me."""

    candidate_type: CandidateType | None = None
    phone: str | None = None
    dob: str | None = None
    gender: str | None = Field(None, pattern="^(male|female|other)$")
    institution: str | None = None
    location: str | None = None


class UpdateCandidateRequest(BaseModel):
    """Super admin or admin update of a candidate's profile."""

    first_name: str | None = Field(None, min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr | None = None
    is_active: bool | None = None
    candidate_data: CandidateDataUpdateRequest | None = None


class UpdateMeRequest(BaseModel):
    """Authenticated user updating their own profile."""

    first_name: str | None = Field(None, min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr | None = None
    password: str | None = Field(None, min_length=8)
    current_password: str | None = None
    default_workspace_id: str | None = None
    candidate_data: CandidateDataUpdateRequest | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str | None) -> str | None:
        if v is not None:
            return check_password_strength(v)
        return v
