from pydantic import BaseModel, Field

from app.common.constants.app_constants import MalpracticeType


class SubmitAnswerRequest(BaseModel):
    question_id: str = Field(..., min_length=1)
    answer: str | list[str]


class MalpracticeRequest(BaseModel):
    type: MalpracticeType
    description: str | None = None


class FinishRoundRequest(BaseModel):
    """Optional body: which round the client believes it is finishing (idempotency guard)."""

    round_number: int | None = Field(default=None, ge=1)
