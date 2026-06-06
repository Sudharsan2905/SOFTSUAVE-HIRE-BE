from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, Request, UploadFile

from app.common.constants.app_constants import MalpracticeType
from app.common.exceptions import ValidationException
from app.common.responses import ApiResponse, success_response
from app.components.auth.auth_dependencies import AdminUser, CurrentUser
from app.components.candidate import candidate_service
from app.components.candidate.candidate_schemas import SubmitAnswerRequest
from app.core.dependencies import DB
from app.core.limiter import limiter

router = APIRouter()

_JPEG = "image/jpeg"
_ALLOWED_IMAGE_TYPES = {_JPEG, "image/png"}
_ALLOWED_VIDEO_TYPES = {"video/webm", "video/mp4"}
_ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mpeg"}
_MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_VIDEO_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB


@router.get("/assessment/{share_link}", response_model=ApiResponse)
@limiter.limit("30/minute")
async def get_assessment(
    request: Request,
    share_link: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
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
    background_tasks: BackgroundTasks,
) -> dict:
    result = await candidate_service.finish_round(db, submission_id, current_user["_id"])
    if result.get("completed"):
        try:
            from app.components.scoring import scoring_tasks

            background_tasks.add_task(scoring_tasks.calculate_and_store_score, db, submission_id)
        except ImportError:
            pass  # scoring module not yet available
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
        db, submission_id, current_user["_id"], content, file.content_type or _JPEG
    )
    return success_response("Screenshot saved")


@router.post("/submission/{submission_id}/malpractice", response_model=ApiResponse)
@limiter.limit("10/minute")
async def flag_malpractice(
    request: Request,
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
    type: Annotated[str, Form()],
    screen_image: Annotated[UploadFile | None, File()] = None,
    face_image: Annotated[UploadFile | None, File()] = None,
    video_chunk: Annotated[UploadFile | None, File()] = None,
    audio_clip: Annotated[UploadFile | None, File()] = None,
) -> dict:
    try:
        malpractice_type = MalpracticeType(type)
    except ValueError as err:
        raise ValidationException(f"Invalid malpractice type: {type!r}") from err

    file_bytes_map: dict = {}

    async def _read_file(
        upload: UploadFile | None, allowed_types: set, max_bytes: int, field: str
    ) -> None:
        if upload is None:
            return
        if upload.content_type not in allowed_types:
            raise ValidationException(f"{field} has invalid content type")
        content = await upload.read()
        if len(content) > max_bytes:
            raise ValidationException(f"{field} exceeds size limit")
        file_bytes_map[field] = (
            content,
            upload.content_type or "application/octet-stream",
        )

    await _read_file(screen_image, _ALLOWED_IMAGE_TYPES, _MAX_SCREENSHOT_BYTES, "screen_image")
    await _read_file(face_image, _ALLOWED_IMAGE_TYPES, _MAX_SCREENSHOT_BYTES, "face_image")
    await _read_file(video_chunk, _ALLOWED_VIDEO_TYPES, _MAX_VIDEO_BYTES, "video_chunk")
    await _read_file(audio_clip, _ALLOWED_AUDIO_TYPES, _MAX_AUDIO_BYTES, "audio_clip")

    result = await candidate_service.flag_malpractice(
        db, submission_id, current_user["_id"], malpractice_type, file_bytes_map
    )
    return success_response("Activity flagged", result)


@router.post("/submission/{submission_id}/livekit-token", response_model=ApiResponse)
async def get_livekit_token(
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    try:
        from app.components.livekit import livekit_service

        result = await livekit_service.generate_candidate_token(
            db, submission_id, str(current_user["_id"])
        )
        return success_response("LiveKit token generated", result)
    except Exception as exc:
        from app.common.exceptions import AppException

        raise AppException(status_code=503, message=f"LiveKit unavailable: {exc}") from exc


@router.get("/submission/{submission_id}/session-state", response_model=ApiResponse)
async def get_session_state(
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    """Return persisted timer/position state for resume after network loss."""
    result = await candidate_service.get_session_state(db, submission_id, current_user["_id"])
    return success_response("Session state retrieved", result)


@router.get("/submission/status", response_model=ApiResponse)
async def get_submission_status(
    share_link: Annotated[str, Query()],
    db: DB,
    current_user: CurrentUser,
) -> dict:
    """Return the current submission status for the authenticated candidate.

    Returns null data when no submission exists yet (assessment not yet started).
    Used by the frontend route guard to determine candidate access before entering
    the assessment flow.
    """
    result = await candidate_service.get_submission_status(db, share_link, current_user["_id"])
    return success_response("Submission status retrieved", result)


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
