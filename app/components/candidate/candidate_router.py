from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile

from app.common.exceptions import ValidationException
from app.common.responses import ApiResponse, success_response
from app.components.auth.auth_dependencies import AdminUser, CurrentUser
from app.components.candidate import candidate_service
from app.components.candidate.candidate_schemas import (
    MalpracticeRequest,
    SubmitAnswerRequest,
)
from app.core.dependencies import DB
from app.core.limiter import limiter

router = APIRouter()

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}
_MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024  # 2 MB


@router.get("/assessment/{share_link}", response_model=ApiResponse)
@limiter.limit("30/minute")
async def get_assessment(request: Request, share_link: str, db: DB) -> dict:
    result = await candidate_service.get_candidate_assessment(db, share_link)
    return success_response("Assessment retrieved", result)


@router.post("/assessment/{share_link}/start", response_model=ApiResponse)
@limiter.limit("5/hour")
async def start_assessment(
    request: Request,
    share_link: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.start_assessment(db, share_link, current_user["_id"])
    return success_response("Assessment started", result)


@router.get("/submission/{submission_id}/round", response_model=ApiResponse)
async def get_current_round(
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.get_current_round(db, submission_id, current_user["_id"])
    return success_response("Round retrieved", result)


@router.post("/submission/{submission_id}/answer", response_model=ApiResponse)
async def submit_answer(
    submission_id: str,
    request: SubmitAnswerRequest,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.submit_answer(
        db, submission_id, current_user["_id"], request.question_id, request.answer
    )
    return success_response("Answer saved", result)


@router.post("/submission/{submission_id}/finish-round", response_model=ApiResponse)
async def finish_round(
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.finish_round(db, submission_id, current_user["_id"])
    return success_response("Round finished", result)


@router.post("/submission/{submission_id}/screenshot", response_model=ApiResponse)
@limiter.limit("30/minute")
async def save_screenshot(
    request: Request,
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> dict:
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise ValidationException("Screenshot must be a JPEG or PNG image")
    content = await file.read()
    if len(content) > _MAX_SCREENSHOT_BYTES:
        raise ValidationException("Screenshot must not exceed 2 MB")
    await candidate_service.save_screenshot(
        db, submission_id, current_user["_id"], content, file.content_type or "image/jpeg"
    )
    return success_response("Screenshot saved")


@router.post("/submission/{submission_id}/malpractice", response_model=ApiResponse)
@limiter.limit("10/minute")
async def flag_malpractice(
    http_request: Request,
    submission_id: str,
    request: MalpracticeRequest,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    await candidate_service.flag_malpractice(db, submission_id, current_user["_id"], request.type)
    return success_response("Activity flagged")


@router.get("/live-interviews", response_model=ApiResponse)
async def get_live_interviews(
    db: DB,
    current_user: AdminUser,
    search: Annotated[str | None, Query()] = None,
    monitoring_type: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str, Query()] = "started_at",
    sort_order: Annotated[str, Query()] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    result = await candidate_service.get_live_interviews(
        db, search, monitoring_type, sort_by, sort_order, page, page_size
    )
    return success_response("Live interviews retrieved", result)
