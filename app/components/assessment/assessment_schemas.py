from pydantic import BaseModel, Field
from typing import Optional, List
from app.common.constants.app_constants import AssessmentAccessibility


class MonitoringConfig(BaseModel):
    tab_monitoring: bool = True
    voice_monitoring: bool = True
    camera_enabled: bool = True
    screenshot_mode: str = Field("time_interval", pattern="^(time_interval|count)$")
    screenshot_interval_minutes: Optional[int] = Field(None, ge=1)
    screenshot_count: Optional[int] = Field(None, ge=1)


class RoundConfig(BaseModel):
    round_number: int = Field(..., ge=1)
    question_count: int = Field(..., ge=1)
    max_duration_minutes: int = Field(..., ge=1)
    question_ids: List[str] = []


class CreateAssessmentRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    rounds: List[RoundConfig] = Field(..., min_length=1)
    accessibility: AssessmentAccessibility = AssessmentAccessibility.NORMAL
    monitoring_config: Optional[MonitoringConfig] = None


class UpdateAssessmentRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    description: Optional[str] = None
    rounds: Optional[List[RoundConfig]] = None
    accessibility: Optional[AssessmentAccessibility] = None
    monitoring_config: Optional[MonitoringConfig] = None
