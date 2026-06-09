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


# ── Candidate session response models ─────────────────────────────────────────


class AnswerSavedResponse(BaseModel):
    saved: bool


class MalpracticeRecordResponse(BaseModel):
    malpractice_count: int
    is_terminal: bool
    event_index: int | None = None
    current_round: int


class SessionStateResponse(BaseModel):
    status: str
    remaining_seconds: int | None = None
    current_question_idx: int
    current_round: int


class SubmissionStatusResponse(BaseModel):
    submission_id: str
    status: str
    assessment_id: str
    candidate_id: str
    current_round: int
    completed_at: str | None = None
    paused_at: str | None = None
    malpractice_count: int
