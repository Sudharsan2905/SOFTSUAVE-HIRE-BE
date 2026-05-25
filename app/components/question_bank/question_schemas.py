from pydantic import BaseModel, Field
from typing import Optional, List
from app.common.constants.app_constants import QuestionType, Complexity


class CreateCategoryRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class UpdateCategoryRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None


class QuestionOption(BaseModel):
    id: str
    text: str
    is_correct: bool = False


class CreateQuestionRequest(BaseModel):
    question_text: str = Field(..., min_length=5)
    question_type: QuestionType
    complexity: Complexity
    options: Optional[List[QuestionOption]] = None
    correct_answer: Optional[str] = None


class UpdateQuestionRequest(BaseModel):
    question_text: Optional[str] = None
    question_type: Optional[QuestionType] = None
    complexity: Optional[Complexity] = None
    options: Optional[List[QuestionOption]] = None
    correct_answer: Optional[str] = None


class BulkQuestionItem(BaseModel):
    question_text: str
    question_type: QuestionType
    complexity: Complexity
    options: Optional[List[QuestionOption]] = None
    correct_answer: Optional[str] = None


class BulkCreateRequest(BaseModel):
    questions: List[BulkQuestionItem] = Field(..., min_length=1)


class AIGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=2)
    count: int = Field(5, ge=1, le=20)
    complexity: Complexity = Complexity.MEDIUM
    question_type: QuestionType = QuestionType.MCQ_SINGLE


class ExcelColumnMappingRequest(BaseModel):
    question_column: str
    answer_column: Optional[str] = None
    complexity_column: Optional[str] = None
    question_type_column: Optional[str] = None
    default_complexity: Optional[Complexity] = Complexity.MEDIUM
    default_question_type: Optional[QuestionType] = QuestionType.ESSAY
