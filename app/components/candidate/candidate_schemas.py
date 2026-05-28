from typing import Any

from pydantic import BaseModel, Field


class SubmitAnswerRequest(BaseModel):
    question_id: str
    answer: Any
    round_number: int = Field(..., ge=1)


class FinishRoundRequest(BaseModel):
    round_number: int = Field(..., ge=1)


class ScreenshotRequest(BaseModel):
    screenshot_data: str
    round_number: int = Field(..., ge=1)


class MalpracticeRequest(BaseModel):
    reason: str
    details: str | None = None
