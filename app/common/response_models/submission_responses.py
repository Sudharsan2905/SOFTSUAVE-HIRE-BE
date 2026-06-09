from datetime import datetime

from pydantic import BaseModel

from app.common.constants.app_constants import MalpracticeType, SubmissionStatus
from app.common.response_models.user_responses import CandidateProfileResponse


class QuestionOptionResponse(BaseModel):
    id: str
    text: str
    is_correct: bool | None = None


class QuestionAnswerResponse(BaseModel):
    question_id: str
    question_text: str
    question_type: str
    options: list[QuestionOptionResponse] = []
    candidate_answer: list[str] = []
    is_correct: bool | None = None


class RoundDataResponse(BaseModel):
    round_number: int
    score: int = 0
    percentage: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    question_answers: list[QuestionAnswerResponse] = []


class MalpracticeEventResponse(BaseModel):
    type: MalpracticeType
    timestamp: datetime
    round: int
    description: str = ""
    screen_image_url: str | None = None
    face_image_url: str | None = None
    screen_video_url: str | None = None
    audio_clip_url: str | None = None
    is_terminal: bool = False


class ScreenshotResponse(BaseModel):
    url: str
    round: int
    taken_at: datetime


class VersionSummaryResponse(BaseModel):
    version: int
    status: str
    percentage: float
    started_at: datetime | None = None
    completed_at: datetime | None = None
    reaccess_reason: str | None = None


class CandidateSubmissionResponse(BaseModel):
    candidate: CandidateProfileResponse
    submission_id: str
    status: SubmissionStatus
    score: int
    percentage: float
    malpractice_count: int
    reaccess_count: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    current_version: int
    available_versions: list[VersionSummaryResponse]
    rounds: list[RoundDataResponse]
    malpractice_events: list[MalpracticeEventResponse]
    screenshots: list[ScreenshotResponse]
