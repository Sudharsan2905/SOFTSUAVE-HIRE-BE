from datetime import datetime

from pydantic import BaseModel

from app.common.constants.app_constants import CandidateType, UserRole


class AdminUserResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    role: UserRole
    is_active: bool
    workspace_ids: list[str]
    default_workspace_id: str | None = None
    created_at: datetime


class CandidateProfileResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    gender: str | None = None
    dob: str | None = None
    institution: str | None = None
    location: str | None = None
    candidate_type: CandidateType | None = None
    created_at: datetime
