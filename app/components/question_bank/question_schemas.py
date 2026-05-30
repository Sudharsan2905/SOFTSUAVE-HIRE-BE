from pydantic import BaseModel, Field, model_validator

from app.common.constants.app_constants import Complexity, QuestionType


class CreateCategoryRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(None, max_length=500)


class UpdateCategoryRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    description: str | None = None


class QuestionOption(BaseModel):
    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    is_correct: bool = False


def _validate_mcq_options(question_type: QuestionType, options: list[QuestionOption]) -> None:
    """Raise ValueError if MCQ/essay options violate business rules."""
    if question_type in (QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI):
        if not options:
            raise ValueError("MCQ questions must have at least one option")
        correct = [o for o in options if o.is_correct]
        if not correct:
            raise ValueError("MCQ questions must have at least one correct option marked")
        if question_type == QuestionType.MCQ_SINGLE and len(correct) > 1:
            raise ValueError("MCQ single-choice questions must have exactly one correct option")
    elif question_type == QuestionType.ESSAY and options:
        raise ValueError("Essay questions must not have options")


class CreateQuestionRequest(BaseModel):
    question_text: str = Field(..., min_length=5)
    question_type: QuestionType
    complexity: Complexity
    options: list[QuestionOption] | None = None
    correct_answer: str | None = None

    @model_validator(mode="after")
    def validate_options(self) -> "CreateQuestionRequest":
        _validate_mcq_options(self.question_type, self.options or [])
        return self


class UpdateQuestionRequest(BaseModel):
    question_text: str | None = None
    question_type: QuestionType | None = None
    complexity: Complexity | None = None
    options: list[QuestionOption] | None = None
    correct_answer: str | None = None

    @model_validator(mode="after")
    def validate_options(self) -> "UpdateQuestionRequest":
        if self.question_type is not None and self.options is not None:
            _validate_mcq_options(self.question_type, self.options)
        return self


class BulkQuestionItem(BaseModel):
    question_text: str
    question_type: QuestionType
    complexity: Complexity
    options: list[QuestionOption] | None = None
    correct_answer: str | None = None

    @model_validator(mode="after")
    def validate_options(self) -> "BulkQuestionItem":
        _validate_mcq_options(self.question_type, self.options or [])
        return self


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
