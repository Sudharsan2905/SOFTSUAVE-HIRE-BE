import re

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.common.constants.app_constants import CandidateType
from app.common.validators import check_password_strength


class SetupRequest(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return check_password_strength(v)


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)


class CandidateRegisterRequest(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=50)
    last_name: str | None = Field(None, max_length=50)
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=15)
    password: str | None = Field(None, min_length=8)
    gender: str = Field(..., pattern="^(male|female|other)$")
    candidate_type: CandidateType = CandidateType.STUDENT
    dob: str | None = None
    institution: str | None = None
    location: str | None = None
    assessment_uuid: str | None = None
    google_id: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str | None) -> str | None:
        if v is not None:
            return check_password_strength(v)
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = re.sub(r"[\s\-\+\(\)]", "", v)
        if not cleaned.isdigit():
            raise ValueError("Phone number must contain only digits")
        return cleaned


class CandidateLoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    credential: str = Field(..., min_length=1)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)
