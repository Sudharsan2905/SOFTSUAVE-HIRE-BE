from pydantic import BaseModel, Field

from app.common.constants.app_constants import MalpracticeType


class SubmitAnswerRequest(BaseModel):
    question_id: str = Field(..., min_length=1)
    answer: str | list[str]


class MalpracticeRequest(BaseModel):
    type: MalpracticeType
    description: str | None = None
