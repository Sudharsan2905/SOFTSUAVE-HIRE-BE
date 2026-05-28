import json
from fastapi import APIRouter, Depends, Query, UploadFile, File, Form
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.dependencies import get_db
from app.components.auth.auth_dependencies import require_admin
from app.components.question_bank import question_service
from app.components.question_bank.question_schemas import (
    CreateCategoryRequest,
    UpdateCategoryRequest,
    CreateQuestionRequest,
    UpdateQuestionRequest,
    BulkCreateRequest,
    AIGenerateRequest,
)
from app.common.responses import success_response

router = APIRouter()


@router.get("/categories")
async def list_categories(
    search: str = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await question_service.get_categories(db, search, sort_by, sort_order, page, page_size)
    return success_response("Categories retrieved", result)


@router.post("/categories")
async def create_category(
    request: CreateCategoryRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await question_service.create_category(db, request.model_dump(), current_user["_id"])
    return success_response("Category created", result)


@router.put("/categories/{category_id}")
async def update_category(
    category_id: str,
    request: UpdateCategoryRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await question_service.update_category(db, category_id, request.model_dump())
    return success_response("Category updated", result)


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    await question_service.delete_category(db, category_id)
    return success_response("Category and its questions deleted")


@router.get("/categories/{category_id}/questions")
async def list_questions(
    category_id: str,
    search: str = Query(None),
    complexity: str = Query(None),
    question_type: str = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await question_service.get_questions(
        db, category_id, search, complexity, question_type, sort_by, sort_order, page, page_size
    )
    return success_response("Questions retrieved", result)


@router.post("/categories/{category_id}/questions")
async def create_question(
    category_id: str,
    request: CreateQuestionRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await question_service.create_question(
        db, category_id, request.model_dump(), current_user["_id"]
    )
    return success_response("Question created", result)


@router.post("/categories/{category_id}/bulk")
async def bulk_create(
    category_id: str,
    request: BulkCreateRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await question_service.bulk_create_questions(
        db, category_id, [q.model_dump() for q in request.questions], current_user["_id"]
    )
    return success_response("Questions created in bulk", result)


@router.post("/categories/{category_id}/ai-generate")
async def ai_generate(
    category_id: str,
    request: AIGenerateRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await question_service.ai_generate_questions(
        db,
        category_id,
        request.topic,
        request.count,
        request.complexity,
        request.question_type,
        current_user["_id"],
    )
    return success_response("AI questions generated", result)


@router.post("/categories/{category_id}/excel-import")
async def excel_import(
    category_id: str,
    file: UploadFile = File(...),
    column_map: str = Form(default="{}"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    file_data = await file.read()
    try:
        col_map = json.loads(column_map)
    except Exception:
        col_map = {}
    result = await question_service.process_excel_import(
        db, category_id, file_data, current_user["_id"], col_map
    )
    return success_response("Excel import completed", result)


@router.put("/{question_id}")
async def update_question(
    question_id: str,
    request: UpdateQuestionRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await question_service.update_question(db, question_id, request.model_dump())
    return success_response("Question updated", result)


@router.delete("/{question_id}")
async def delete_question(
    question_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    await question_service.delete_question(db, question_id)
    return success_response("Question deleted")
