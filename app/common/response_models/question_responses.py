from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.common.constants.app_constants import QuestionType


class CategoryResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    question_count: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class QuestionOptionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    is_correct: bool = False


class QuestionResponse(BaseModel):
    id: str
    category_id: str
    question_text: str
    question_type: QuestionType
    complexity: str
    options: list[QuestionOptionResponse] = []
    correct_answer: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class BulkOperationResponse(BaseModel):
    created: int
    error: str | None = None
