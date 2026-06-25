from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator

from app.common.constants.app_constants import AssessmentAccessibility


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
    submissions_30d: int
    avg_completion: int


class SubmissionStatsResponse(BaseModel):
    total: int
    completed: int
    on_hold: int
    malpractice: int
    avg_percentage: float


class AssessmentListItemResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    accessibility: AssessmentAccessibility
    rounds_count: int
    submission_count: int = 0
    expected_candidates: int | None = None
    share_link: str
    created_at: datetime


class AssessmentDetailResponse(AssessmentListItemResponse):
    rounds: list[RoundConfigResponse] = []
    monitoring_config: MonitoringConfigResponse | None = None

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
