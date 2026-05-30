from pydantic import BaseModel, Field, model_validator

from app.common.constants.app_constants import AssessmentAccessibility


class MonitoringConfig(BaseModel):
    tab_monitoring: bool = True
    voice_monitoring: bool = True
    camera_enabled: bool = True
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
