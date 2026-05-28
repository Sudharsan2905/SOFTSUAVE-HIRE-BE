from pydantic import BaseModel, Field

from app.common.constants.app_constants import Complexity, QuestionType


class CreateCategoryRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(None, max_length=500)


class UpdateCategoryRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    description: str | None = None


class QuestionOption(BaseModel):
    id: str
    text: str
    is_correct: bool = False


class CreateQuestionRequest(BaseModel):
    question_text: str = Field(..., min_length=5)
    question_type: QuestionType
    complexity: Complexity
    options: list[QuestionOption] | None = None
    correct_answer: str | None = None


class UpdateQuestionRequest(BaseModel):
    question_text: str | None = None
    question_type: QuestionType | None = None
    complexity: Complexity | None = None
    options: list[QuestionOption] | None = None
    correct_answer: str | None = None


class BulkQuestionItem(BaseModel):
    question_text: str
    question_type: QuestionType
    complexity: Complexity
    options: list[QuestionOption] | None = None
    correct_answer: str | None = None


class BulkCreateRequest(BaseModel):
    questions: list[BulkQuestionItem] = Field(..., min_length=1)


class AIGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=2)
    count: int = Field(5, ge=1, le=20)
    complexity: Complexity = Complexity.MEDIUM
    question_type: QuestionType = QuestionType.MCQ_SINGLE


class ExcelColumnMappingRequest(BaseModel):
    question_column: str
    answer_column: str | None = None
    complexity_column: str | None = None
    question_type_column: str | None = None
    default_complexity: Complexity | None = Complexity.MEDIUM
    default_question_type: QuestionType | None = QuestionType.ESSAY
