from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
import re


class SetupRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8)


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class CandidateRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=15)
    password: str = Field(..., min_length=8)
    confirm_password: str
    father_name: str = Field(..., min_length=2, max_length=100)
    gender: str = Field(..., pattern="^(male|female|other)$")
    dob: Optional[str] = None
    college_name: Optional[str] = None
    college_city: Optional[str] = None
    assessment_uuid: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("Password must contain at least one special character")
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
    code: str
    assessment_uuid: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str
