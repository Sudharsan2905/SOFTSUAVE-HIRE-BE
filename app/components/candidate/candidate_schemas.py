from typing import Any

from pydantic import BaseModel


class SubmitAnswerRequest(BaseModel):
    question_id: str
    answer: Any


class MalpracticeRequest(BaseModel):
    type: str
