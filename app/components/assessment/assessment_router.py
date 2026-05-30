from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.common.responses import ApiResponse, success_response
from app.components.assessment import assessment_service
from app.components.assessment.assessment_schemas import (
    CreateAssessmentRequest,
    GenerateExpirableLinkRequest,
    UpdateAssessmentRequest,
)
from app.components.auth.auth_dependencies import AdminUser
from app.core.dependencies import DB
from app.core.limiter import limiter

router = APIRouter()


@router.get("/workspaces/{workspace_id}/assessments", response_model=ApiResponse)
async def list_assessments(
    workspace_id: str,
    db: DB,
    current_user: AdminUser,
    search: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str, Query()] = "created_at",
    sort_order: Annotated[str, Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    result = await assessment_service.get_assessments(
        db, workspace_id, search, sort_by, sort_order, page, page_size
    )
    return success_response("Assessments retrieved", result)


@router.post("/workspaces/{workspace_id}/assessments", response_model=ApiResponse)
async def create_assessment(
    workspace_id: str,
    request: CreateAssessmentRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await assessment_service.create_assessment(
        db, workspace_id, request.model_dump(), current_user["_id"]
    )
    return success_response("Assessment created", result)


@router.get("/workspaces/{workspace_id}/assessments/{assessment_id}", response_model=ApiResponse)
async def get_assessment(
    workspace_id: str,
    assessment_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await assessment_service.get_assessment(db, workspace_id, assessment_id)
    return success_response("Assessment retrieved", result)


@router.put("/workspaces/{workspace_id}/assessments/{assessment_id}", response_model=ApiResponse)
async def update_assessment(
    workspace_id: str,
    assessment_id: str,
    request: UpdateAssessmentRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await assessment_service.update_assessment(
        db, workspace_id, assessment_id, request.model_dump()
    )
    return success_response("Assessment updated", result)


@router.get(
    "/workspaces/{workspace_id}/assessments/{assessment_id}/submissions",
    response_model=ApiResponse,
)
async def list_submissions(
    workspace_id: str,
    assessment_id: str,
    db: DB,
    current_user: AdminUser,
    search: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str, Query()] = "created_at",
    sort_order: Annotated[str, Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    from_date: Annotated[str | None, Query()] = None,
    to_date: Annotated[str | None, Query()] = None,
) -> dict:
    result = await assessment_service.get_submissions(
        db,
        assessment_id,
        search,
        sort_by,
        sort_order,
        page,
        page_size,
        from_date=from_date,
        to_date=to_date,
    )
    return success_response("Submissions retrieved", result)


@router.get(
    "/workspaces/{workspace_id}/assessments/{assessment_id}/submissions/export",
    response_model=ApiResponse,
)
async def export_submission_list(
    workspace_id: str,
    assessment_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await assessment_service.export_submissions(db, assessment_id)
    return success_response("Export data retrieved", result)


@router.get(
    "/workspaces/{workspace_id}/assessments/{assessment_id}/submissions/{submission_id}",
    response_model=ApiResponse,
)
async def get_submission(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await assessment_service.get_submission_detail(db, submission_id)
    return success_response("Submission retrieved", result)


@router.post(
    "/workspaces/{workspace_id}/assessments/{assessment_id}/submissions/{submission_id}/reaccess",
    response_model=ApiResponse,
)
async def grant_reaccess(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    await assessment_service.grant_reaccess(db, submission_id)
    return success_response("Re-access granted successfully")


@router.get(
    "/workspaces/{workspace_id}/assessments/{assessment_id}/export",
    response_model=ApiResponse,
)
@limiter.limit("10/hour")
async def export_submissions(
    request: Request,
    workspace_id: str,
    assessment_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await assessment_service.export_submissions(db, assessment_id)
    return success_response("Export data retrieved", result)


@router.post("/assessments/share/expirable", response_model=ApiResponse)
@limiter.limit("10/hour")
async def generate_expirable_share_link(
    request: Request,
    workspace_id: Annotated[str, Query()],
    body: GenerateExpirableLinkRequest,
    db: DB,
    current_user: AdminUser,
):
    link = await assessment_service.generate_expirable_link(
        db, body.assessment_id, workspace_id, body.start_time, body.end_time
    )
    return success_response("Expirable link generated", {"share_link": link})


@router.get("/assessments/share/validate", response_model=ApiResponse)
@limiter.limit("60/minute")
async def validate_share_link(
    request: Request,
    link: Annotated[str, Query()],
    db: DB,
):
    result = await assessment_service.validate_sharelink(db, link)
    return success_response("Share link validated", result)


@router.get("/assessments/share/{share_link}", response_model=ApiResponse)
@limiter.limit("30/minute")
async def get_by_share_link(request: Request, share_link: str, db: DB) -> dict:
    result = await assessment_service.get_assessment_by_share_link(db, share_link)
    return success_response("Assessment retrieved", result)
