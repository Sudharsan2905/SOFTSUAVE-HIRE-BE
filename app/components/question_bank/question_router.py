import json
from typing import Annotated

from fastapi import File, Form, Query, Request, UploadFile

from app.common.constants.messages import SuccessMessages
from app.common.response_models.question_responses import (
    BulkOperationResponse,
    CategoryResponse,
    QuestionResponse,
)
from app.common.responses import ApiResponse, success_response
from app.common.router import DefaultResponseRouter
from app.components.auth.auth_dependencies import AdminUser
from app.components.question_bank import question_service
from app.components.question_bank.question_schemas import (
    AIGenerateRequest,
    BulkCreateRequest,
    CreateCategoryRequest,
    CreateQuestionRequest,
    UpdateCategoryRequest,
    UpdateQuestionRequest,
)
from app.core.dependencies import DB
from app.core.limiter import limiter

router = DefaultResponseRouter()


@router.get("/categories")
async def list_categories(
    db: DB,
    current_user: AdminUser,
    search: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str, Query()] = "created_at",
    sort_order: Annotated[str, Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    result = await question_service.get_categories(db, search, sort_by, sort_order, page, page_size)
    return success_response(SuccessMessages.CATEGORIES_RETRIEVED, result)


@router.post("/categories", response_model=ApiResponse[CategoryResponse])
async def create_category(
    request: CreateCategoryRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await question_service.create_category(db, request.model_dump(), current_user["_id"])
    return success_response(SuccessMessages.CATEGORY_CREATED, result)


@router.put("/categories/{category_id}", response_model=ApiResponse[CategoryResponse])
async def update_category(
    category_id: str,
    request: UpdateCategoryRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await question_service.update_category(db, category_id, request.model_dump())
    return success_response(SuccessMessages.CATEGORY_UPDATED, result)


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    await question_service.delete_category(db, category_id)
    return success_response(SuccessMessages.CATEGORY_DELETED)


@router.get("/categories/{category_id}/questions")
async def list_questions(
    category_id: str,
    db: DB,
    current_user: AdminUser,
    search: Annotated[str | None, Query()] = None,
    complexity: Annotated[str | None, Query()] = None,
    question_type: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str, Query()] = "created_at",
    sort_order: Annotated[str, Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    result = await question_service.get_questions(
        db, category_id, search, complexity, question_type, sort_by, sort_order, page, page_size
    )
    return success_response(SuccessMessages.QUESTIONS_RETRIEVED, result)


@router.post("/categories/{category_id}/questions", response_model=ApiResponse[QuestionResponse])
async def create_question(
    category_id: str,
    request: CreateQuestionRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await question_service.create_question(
        db, category_id, request.model_dump(), current_user["_id"]
    )
    return success_response(SuccessMessages.QUESTION_CREATED, result)


@router.post("/categories/{category_id}/bulk", response_model=ApiResponse[BulkOperationResponse])
async def bulk_create(
    category_id: str,
    request: BulkCreateRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await question_service.bulk_create_questions(
        db, category_id, [q.model_dump() for q in request.questions], current_user["_id"]
    )
    return success_response(SuccessMessages.BULK_QUESTIONS_CREATED, result)


@router.post(
    "/categories/{category_id}/ai-generate", response_model=ApiResponse[BulkOperationResponse]
)
@limiter.limit("10/hour")
async def ai_generate(
    request: Request,
    category_id: str,
    body: AIGenerateRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await question_service.ai_generate_questions(
        db,
        category_id,
        body.topic,
        body.count,
        body.complexity,
        body.question_type,
        current_user["_id"],
    )
    return success_response(SuccessMessages.QUESTIONS_GENERATED, result)


@router.post(
    "/categories/{category_id}/excel-import", response_model=ApiResponse[BulkOperationResponse]
)
@limiter.limit("20/hour")
async def excel_import(
    request: Request,
    category_id: str,
    db: DB,
    current_user: AdminUser,
    file: Annotated[UploadFile, File()],
    column_map: Annotated[str, Form()] = "{}",
) -> dict:
    file_data = await file.read()
    try:
        col_map = json.loads(column_map)
    except Exception:
        col_map = {}
    result = await question_service.process_excel_import(
        db, category_id, file_data, current_user["_id"], col_map
    )
    return success_response(SuccessMessages.QUESTIONS_IMPORTED, result)


@router.put("/{question_id}", response_model=ApiResponse[QuestionResponse])
async def update_question(
    question_id: str,
    request: UpdateQuestionRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await question_service.update_question(db, question_id, request.model_dump())
    return success_response(SuccessMessages.QUESTION_UPDATED, result)


@router.delete("/{question_id}")
async def delete_question(
    question_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    await question_service.delete_question(db, question_id)
    return success_response(SuccessMessages.QUESTION_DELETED)
