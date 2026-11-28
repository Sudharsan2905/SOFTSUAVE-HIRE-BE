from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator

from app.common.constants.app_constants import DEFAULT_PASS_PERCENTAGE, AssessmentAccessibility
from app.common.schemas.pagination import PaginationMeta


class MonitoringConfigResponse(BaseModel):
    tab_monitoring: bool = False
    audio_monitoring: bool = False
    video_monitoring: bool = False
    screenshot_enabled: bool = False
    screenshot_mode: str = "time_interval"
    screenshot_interval_minutes: int | None = None
    screenshot_count: int | None = None


class RoundConfigResponse(BaseModel):
    round_number: int
    question_count: int
    max_duration_minutes: int


class AssessmentStatsResponse(BaseModel):
    total: int
    monitoring: int
    standard: int
    active_now: int = 0
    submissions_30d: int
    avg_completion: int


class SubmissionStatsResponse(BaseModel):
    total: int
    completed: int
    on_hold: int
    malpractice: int
    avg_percentage: float
    pass_percentage: int = DEFAULT_PASS_PERCENTAGE
    passed: int = 0
    failed: int = 0


class ScoreFilterAssessment(BaseModel):
    id: str
    name: str


class ScoreFilterSummary(BaseModel):
    """Counts over every entry matching the search, before the result filter is applied."""

    total: int = 0
    completed: int = 0
    passed: int = 0
    failed: int = 0
    incomplete: int = 0


class ScoreFilterCandidate(BaseModel):
    id: str
    name: str
    email: str | None = None


class ScoreFilterEntry(BaseModel):
    """One candidate's submission in one selected assessment (a flat row).

    A candidate who took several of the selected assessments appears once per assessment.
    ``status`` is the submission status, or ``scoring_pending`` (completed, but scoring has not
    finished). ``score`` / ``total`` / ``percentage`` are only set once the submission is final.
    """

    candidate: ScoreFilterCandidate
    assessment: ScoreFilterAssessment
    status: str
    score: int | None = None
    total: int | None = None
    percentage: float | None = None
    # "passed" / "failed" (needs a pass percentage), "incomplete", or None (final, no mark yet)
    result: str | None = None


class ScoreFilterResponse(BaseModel):
    assessments: list[ScoreFilterAssessment]
    pass_percentage: float | None = None
    summary: ScoreFilterSummary
    entries: list[ScoreFilterEntry]
    pagination: PaginationMeta


class AssessmentListItemResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    accessibility: AssessmentAccessibility
    rounds_count: int
    submission_count: int = 0
    expected_candidates: int | None = None
    pass_percentage: int = DEFAULT_PASS_PERCENTAGE
    share_link: str
    created_at: datetime


class AssessmentDetailResponse(AssessmentListItemResponse):
    rounds: list[RoundConfigResponse] = []
    monitoring_config: MonitoringConfigResponse | None = None
    restrict_candidate_access: bool | None = False
    restriction_mode: str | None = None
    restricted_emails: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def derive_rounds_count(cls, v: Any) -> Any:
        if isinstance(v, dict) and "rounds_count" not in v:
            v = dict(v)
            v["rounds_count"] = len(v.get("rounds") or [])
        return v


class ShareLinkResponse(BaseModel):
    id: str
    label: str
    share_link: str
    monitoring_overrides: dict | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    is_active: bool
    created_at: datetime
