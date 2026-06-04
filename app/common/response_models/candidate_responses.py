from datetime import datetime

from pydantic import BaseModel

from app.common.constants.app_constants import CandidateType, SubmissionStatus


class CandidateBasicResponse(BaseModel):
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


class CandidateListItemResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    status: SubmissionStatus
    score: int
    percentage: float
    malpractice_count: int
    started_at: datetime | None = None
    completed_at: datetime | None = None


class CandidateRoundSummaryResponse(BaseModel):
    round_number: int
    score: int
    percentage: float
    started_at: datetime | None = None
    completed_at: datetime | None = None
