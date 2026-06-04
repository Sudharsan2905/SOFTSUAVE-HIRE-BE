from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.common.constants.app_constants import UserRole
from app.common.validators import check_password_strength

GENDER_PATTERN = "^(male|female|other|prefer_not_to_say)$"


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


class UpdateCandidateRequest(BaseModel):
    """Super admin or admin update of a candidate's profile."""

    first_name: str | None = Field(None, min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr | None = None
    is_active: bool | None = None
    phone: str | None = None
    dob: str | None = None
    gender: str | None = Field(None, pattern=GENDER_PATTERN)
    institution: str | None = None
    location: str | None = None


class CreateCandidateAdminRequest(BaseModel):
    """Admin creates a candidate account directly (e.g. from the Schedule Wizard)."""

    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr
    phone: str | None = Field(None, max_length=30)
    gender: str | None = Field(None, pattern=GENDER_PATTERN)
    dob: str | None = None  # ISO date string e.g. "1998-04-15"
    institution: str | None = None  # college / company name
    location: str | None = None  # city / location


class UpdateMeRequest(BaseModel):
    """Authenticated user updating their own profile."""

    first_name: str | None = Field(None, min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr | None = None
    password: str | None = Field(None, min_length=8)
    current_password: str | None = None
    default_workspace_id: str | None = None
    phone: str | None = None
    dob: str | None = None
    gender: str | None = Field(None, pattern=GENDER_PATTERN)
    institution: str | None = None
    location: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str | None) -> str | None:
        if v is not None:
            return check_password_strength(v)
        return v


class CreateCandidateBulkEntry(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    phone: str | None = Field(None, max_length=30)
    gender: str | None = Field(None, pattern=GENDER_PATTERN)
    dob: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return check_password_strength(v)


class BulkCreateCandidatesRequest(BaseModel):
    candidates: list[CreateCandidateBulkEntry] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_unique_emails(self) -> "BulkCreateCandidatesRequest":
        emails = [str(c.email).lower() for c in self.candidates]
        if len(emails) != len(set(emails)):
            raise ValueError("Duplicate email addresses found")
        return self
