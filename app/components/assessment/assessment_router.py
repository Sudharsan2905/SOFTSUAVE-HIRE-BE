from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from app.common.responses import ApiResponse, success_response
from app.components.assessment import assessment_service
from app.components.assessment.assessment_schemas import (
    CreateAssessmentRequest,
    CreateShareRequest,
    ReaccessRequest,
    TerminateSubmissionRequest,
    UpdateAssessmentRequest,
)
from app.components.auth.auth_dependencies import AdminUser
from app.core.dependencies import DB
from app.core.limiter import limiter

router = APIRouter(prefix="/workspaces/{workspace_id}/assessments")


@router.get("", response_model=ApiResponse)
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


@router.post("", response_model=ApiResponse)
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


@router.get("/{assessment_id}", response_model=ApiResponse)
async def get_assessment(
    workspace_id: str,
    assessment_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    result = await assessment_service.get_assessment(db, workspace_id, assessment_id)
    return success_response("Assessment retrieved", result)


@router.delete("/{assessment_id}", response_model=ApiResponse)
async def delete_assessment(
    workspace_id: str,
    assessment_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    await assessment_service.delete_assessment(db, workspace_id, assessment_id)
    return success_response("Assessment deleted")


@router.put("/{assessment_id}", response_model=ApiResponse)
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
    "/{assessment_id}/submissions",
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
    "/{assessment_id}/submissions/export",
    response_model=ApiResponse,
)
async def export_submission_list(
    workspace_id: str,
    assessment_id: str,
    db: DB,
    current_user: AdminUser,
    status: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    min_percentage: Annotated[float | None, Query(ge=0, le=100)] = None,
    max_percentage: Annotated[float | None, Query(ge=0, le=100)] = None,
) -> dict:
    result = await assessment_service.export_submissions(
        db, assessment_id, status, search, min_percentage, max_percentage
    )
    return success_response("Export data retrieved", result)


@router.get(
    "/{assessment_id}/submissions/{submission_id}",
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
    "/{assessment_id}/submissions/{submission_id}/reaccess",
    response_model=ApiResponse,
)
async def grant_reaccess(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    request: ReaccessRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    await assessment_service.grant_reaccess(
        db, submission_id, current_user["_id"], request.reason, request.reason_category
    )
    return success_response("Re-access granted successfully")


@router.post("/{assessment_id}/submissions/{submission_id}/terminate", response_model=ApiResponse)
async def terminate_submission(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    request: TerminateSubmissionRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    from app.components.candidate import candidate_service

    await candidate_service.put_session_terminated(db, submission_id, request.reason)
    return success_response("Session terminated")


@router.post("/{assessment_id}/submissions/{submission_id}/complete", response_model=ApiResponse)
async def force_complete_submission(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    from app.components.candidate import candidate_service

    await candidate_service.put_session_completed(db, submission_id)
    return success_response("Session completed")


@router.post(
    "/{assessment_id}/submissions/{submission_id}/resume",
    response_model=ApiResponse,
)
async def resume_interview(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    """Resume an ON_HOLD candidate session.

    Pushes live WebSocket event if candidate is connected.
    """
    await assessment_service.admin_resume_interview(db, submission_id, current_user["_id"])
    return success_response("Interview resumed successfully")


@router.get("/{assessment_id}/submissions/{submission_id}/pdf")
async def download_submission_pdf(
    workspace_id: str,
    assessment_id: str,
    submission_id: str,
    db: DB,
    current_user: AdminUser,
) -> Response:
    """Generate and return an A4 PDF report for a single submission."""
    from app.components.assessment import assessment_service
    from app.components.export import pdf_service

    detail = await assessment_service.get_submission_detail(db, submission_id)
    candidate = detail.get("candidate", {})
    assessment = await db.assessments.find_one(
        {"_id": __import__("bson").ObjectId(assessment_id), "is_active": True}
    )
    assessment_name = assessment.get("name", "Assessment") if assessment else "Assessment"
    pdf_bytes = pdf_service.generate_submission_pdf(detail, candidate, assessment_name)
    filename = f"submission_{submission_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{assessment_id}/candidates/{candidate_id}/submission", response_model=ApiResponse)
async def get_candidate_submission(
    workspace_id: str,
    assessment_id: str,
    candidate_id: str,
    db: DB,
    current_user: AdminUser,
    version: Annotated[str, Query()] = "current",
) -> dict:
    from app.components.version_history import version_service

    result = await version_service.get_candidate_submission(
        db, assessment_id, candidate_id, version
    )
    return success_response("Candidate submission retrieved", result)


@router.get("/{assessment_id}/candidates/{candidate_id}/versions", response_model=ApiResponse)
async def get_candidate_versions(
    workspace_id: str,
    assessment_id: str,
    candidate_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    from app.components.version_history import version_service

    sub = await db.assessment_submissions.find_one(
        {
            "assessment_id": ObjectId(assessment_id),
            "candidate_id": ObjectId(candidate_id),
        }
    )
    if not sub:
        from app.common.exceptions import NotFoundException

        raise NotFoundException("Submission not found")
    versions = await version_service.get_versions_list(db, str(sub["_id"]))
    return success_response("Versions retrieved", {"versions": versions})


@router.post("/{assessment_id}/shares", response_model=ApiResponse)
async def create_share(
    workspace_id: str,
    assessment_id: str,
    request: CreateShareRequest,
    db: DB,
    current_user: AdminUser,
) -> dict:
    from app.components.assessment import share_service

    result = await share_service.create_share(
        db, assessment_id, workspace_id, request.model_dump(), current_user["_id"]
    )
    return success_response("Share link created", result)


@router.get("/{assessment_id}/shares", response_model=ApiResponse)
async def list_shares(
    workspace_id: str,
    assessment_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    from app.components.assessment import share_service

    result = await share_service.get_shares(db, assessment_id, workspace_id)
    return success_response("Shares retrieved", result)


@router.delete("/{assessment_id}/shares/{share_id}", response_model=ApiResponse)
async def delete_share(
    workspace_id: str,
    assessment_id: str,
    share_id: str,
    db: DB,
    current_user: AdminUser,
) -> dict:
    from app.components.assessment import share_service

    await share_service.delete_share(db, share_id, workspace_id)
    return success_response("Share revoked")


# Public router — no workspace prefix, no auth required
public_router = APIRouter()


@public_router.get("/assessments/share/validate", response_model=ApiResponse)
@limiter.limit("60/minute")
async def validate_share_link(
    request: Request,
    link: Annotated[str, Query()],
    db: DB,
) -> dict:
    result = await assessment_service.validate_sharelink(db, link)
    return success_response("Share link validated", result)
