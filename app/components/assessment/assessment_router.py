from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.responses import success_response
from app.components.assessment import assessment_service
from app.components.assessment.assessment_schemas import (
    CreateAssessmentRequest,
    UpdateAssessmentRequest,
)
from app.components.auth.auth_dependencies import require_admin
from app.core.dependencies import get_db

router = APIRouter()


@router.get("/workspaces/{workspace_id}/assessments")
async def list_assessments(
    workspace_id: str,
    search: str = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await assessment_service.get_assessments(
        db, workspace_id, search, sort_by, sort_order, page, page_size
    )
    return success_response("Assessments retrieved", result)


@router.post("/workspaces/{workspace_id}/assessments")
async def create_assessment(
    workspace_id: str,
    request: CreateAssessmentRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await assessment_service.create_assessment(
        db, workspace_id, request.model_dump(), current_user["_id"]
    )
    return success_response("Assessment created", result)


@router.get("/workspaces/{workspace_id}/assessments/{assessment_id}")
async def get_assessment(
    workspace_id: str,
    assessment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await assessment_service.get_assessment(db, workspace_id, assessment_id)
    return success_response("Assessment retrieved", result)


@router.put("/workspaces/{workspace_id}/assessments/{assessment_id}")
async def update_assessment(
    workspace_id: str,
    assessment_id: str,
    request: UpdateAssessmentRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await assessment_service.update_assessment(
        db, workspace_id, assessment_id, request.model_dump()
    )
    return success_response("Assessment updated", result)


@router.post("/workspaces/{workspace_id}/assessments/{assessment_id}/clone")
async def clone_assessment(
    workspace_id: str,
    assessment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await assessment_service.clone_assessment(
        db, workspace_id, assessment_id, current_user["_id"]
    )
    return success_response("Assessment cloned", result)


@router.get("/workspaces/{workspace_id}/assessments/{assessment_id}/submissions")
async def list_submissions(
    workspace_id: str,
    assessment_id: str,
    search: str = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await assessment_service.get_submissions(
        db, assessment_id, search, sort_by, sort_order, page, page_size
    )
    return success_response("Submissions retrieved", result)


@router.get("/workspaces/{workspace_id}/assessments/{assessment_id}/submissions/{submission_id}")
async def get_submission(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await assessment_service.get_submission_detail(db, submission_id)
    return success_response("Submission retrieved", result)


@router.post(
    "/workspaces/{workspace_id}/assessments/{assessment_id}/submissions/{submission_id}/reaccess"
)
async def grant_reaccess(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    await assessment_service.grant_reaccess(db, submission_id)
    return success_response("Re-access granted successfully")


@router.get("/workspaces/{workspace_id}/assessments/{assessment_id}/export")
async def export_submissions(
    workspace_id: str,
    assessment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await assessment_service.export_submissions(db, assessment_id)
    return success_response("Export data retrieved", result)


@router.get("/assessments/share/{share_link}")
async def get_by_share_link(share_link: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await assessment_service.get_assessment_by_share_link(db, share_link)
    return success_response("Assessment retrieved", result)
