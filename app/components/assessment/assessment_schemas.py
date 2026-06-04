from pydantic import BaseModel, Field, model_validator

from app.common.constants.app_constants import AssessmentAccessibility, ReaccessReasonCategory


class MonitoringConfig(BaseModel):
    tab_monitoring: bool = True
    audio_monitoring: bool = True
    video_monitoring: bool = True
    screenshot_enabled: bool = True
    screenshot_mode: str = Field("time_interval", pattern="^(time_interval|count)$")
    screenshot_interval_minutes: int | None = Field(None, ge=1)
    screenshot_count: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def validate_screenshot_config(self) -> "MonitoringConfig":
        # Only enforce when screenshot_mode was explicitly provided by the caller.
        if "screenshot_mode" not in self.model_fields_set:
            return self
        if self.screenshot_mode == "time_interval" and self.screenshot_interval_minutes is None:
            raise ValueError(
                "screenshot_interval_minutes is required when screenshot_mode is 'time_interval'"
            )
        if self.screenshot_mode == "count" and self.screenshot_count is None:
            raise ValueError("screenshot_count is required when screenshot_mode is 'count'")
        return self


class CandidateMonitoringOverrides(BaseModel):
    """Partial monitoring config — only provided fields override the assessment defaults."""

    tab_monitoring: bool | None = None
    audio_monitoring: bool | None = None
    video_monitoring: bool | None = None
    screenshot_enabled: bool | None = None
    screenshot_mode: str | None = Field(None, pattern="^(time_interval|count)$")
    screenshot_interval_minutes: int | None = Field(None, ge=1)
    screenshot_count: int | None = Field(None, ge=1)


class RoundConfig(BaseModel):
    round_number: int = Field(..., ge=1)
    question_count: int = Field(..., ge=1)
    max_duration_minutes: int = Field(..., ge=1)
    question_ids: list[str] = []


class CreateAssessmentRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    description: str | None = Field(None, max_length=1000)
    rounds: list[RoundConfig] = Field(..., min_length=1)
    accessibility: AssessmentAccessibility = AssessmentAccessibility.NORMAL
    monitoring_config: MonitoringConfig | None = None


class UpdateAssessmentRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=200)
    description: str | None = None
    rounds: list[RoundConfig] | None = None
    accessibility: AssessmentAccessibility | None = None
    monitoring_config: MonitoringConfig | None = None


class GenerateExpirableLinkRequest(BaseModel):
    assessment_id: str
    start_time: str  # ISO 8601
    end_time: str  # ISO 8601


# ─── Re-access ────────────────────────────────────────────────────────────────


class ReaccessRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)
    reason_category: ReaccessReasonCategory = ReaccessReasonCategory.OTHER


class TerminateSubmissionRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


# ─── Share System ─────────────────────────────────────────────────────────────


class CreateShareRequest(BaseModel):
    share_type: str = Field(..., pattern="^(expirable|custom)$")
    label: str | None = Field(None, max_length=100)
    monitoring_overrides: CandidateMonitoringOverrides | None = None
    start_time: str | None = None  # ISO 8601 — required for expirable
    end_time: str | None = None  # ISO 8601 — required for expirable

    @model_validator(mode="after")
    def validate_share_type_fields(self) -> "CreateShareRequest":
        if self.share_type == "expirable" and (not self.start_time or not self.end_time):
            raise ValueError("start_time and end_time are required for expirable links")
        if self.share_type == "custom" and not self.monitoring_overrides:
            raise ValueError("monitoring_overrides is required for custom monitoring links")
        return self
