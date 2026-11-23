from typing import Annotated

from fastapi import BackgroundTasks, File, Form, Query, Request, UploadFile

from app.common import uploads
from app.common.constants.app_constants import MalpracticeType
from app.common.constants.messages import SuccessMessages
from app.common.exceptions import NotFoundException, ValidationException
from app.common.response_models.candidate_responses import (
    AnswerSavedResponse,
    MalpracticeRecordResponse,
    SessionStateResponse,
    SubmissionStatusResponse,
)
from app.common.response_models.livekit_responses import LiveKitTokenResponse
from app.common.responses import ApiResponse, success_response
from app.common.router import DefaultResponseRouter
from app.components.assessment.access import is_super_admin, member_workspace_ids
from app.components.auth.auth_dependencies import AdminUser, CurrentUser
from app.components.candidate import candidate_service
from app.components.candidate.candidate_schemas import FinishRoundRequest, SubmitAnswerRequest
from app.core.dependencies import DB
from app.core.limiter import limiter

router = DefaultResponseRouter()

_JPEG = "image/jpeg"
# Allowed *actual* file types (sniffed from magic bytes — the client Content-Type is ignored).
_ALLOWED_IMAGE_TYPES = {uploads.MIME_JPEG, uploads.MIME_PNG}
_ALLOWED_VIDEO_TYPES = {uploads.MIME_WEBM, uploads.MIME_MP4}
# Browsers record audio clips as WebM/Ogg; MP3 is accepted too.
_ALLOWED_AUDIO_TYPES = {uploads.MIME_WEBM, uploads.MIME_OGG, uploads.MIME_MP3}
_MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_VIDEO_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB


@router.get("/assessment/{share_link}")
@limiter.limit("30/minute")
async def get_assessment(
    request: Request,
    share_link: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.get_candidate_assessment(db, share_link)
    return success_response(SuccessMessages.ASSESSMENT_PAGE_RETRIEVED, result)


@router.post("/assessment/{share_link}/start")
@limiter.limit("5/hour")
async def start_assessment(
    request: Request,
    share_link: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.start_assessment(db, share_link, current_user["_id"])
    return success_response(SuccessMessages.ASSESSMENT_STARTED, result)


@router.get("/submission/{submission_id}/round")
async def get_current_round(
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.get_current_round(db, submission_id, current_user["_id"])
    return success_response(SuccessMessages.ROUND_RETRIEVED, result)


@router.post("/submission/{submission_id}/answer", response_model=ApiResponse[AnswerSavedResponse])
async def submit_answer(
    submission_id: str,
    request: SubmitAnswerRequest,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.submit_answer(
        db, submission_id, current_user["_id"], request.question_id, request.answer
    )
    return success_response(SuccessMessages.ANSWER_SUBMITTED, result)


@router.post("/submission/{submission_id}/start")
async def start_interview(
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.start_interview(db, submission_id, current_user["_id"])
    return success_response(SuccessMessages.INTERVIEW_STARTED, result)


@router.post("/submission/{submission_id}/finish-round")
async def finish_round(
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    body: FinishRoundRequest | None = None,
) -> dict:
    from app.components.scoring import scoring_tasks

    result = await candidate_service.finish_round(
        db, submission_id, current_user["_id"], body.round_number if body else None
    )
    finished_round = result.pop("finished_round")
    # A replayed finish on an already-completed session must not trigger scoring again.
    if not result.pop("already_completed", False):
        background_tasks.add_task(
            scoring_tasks.calculate_and_store_score, db, submission_id, finished_round
        )
    return success_response(SuccessMessages.ROUND_FINISHED, result)


@router.post("/submission/{submission_id}/screenshot")
@limiter.limit("30/minute")
async def save_screenshot(
    request: Request,
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> dict:
    content, mime = await uploads.read_validated(
        file, _ALLOWED_IMAGE_TYPES, _MAX_SCREENSHOT_BYTES, "Screenshot"
    )
    await candidate_service.save_screenshot(db, submission_id, current_user["_id"], content, mime)
    return success_response(SuccessMessages.SCREENSHOT_SAVED)


@router.post(
    "/submission/{submission_id}/malpractice",
    response_model=ApiResponse[MalpracticeRecordResponse],
)
@limiter.limit("10/minute")
async def flag_malpractice(
    request: Request,
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    type: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    screen_image: Annotated[UploadFile | None, File()] = None,
    face_image: Annotated[UploadFile | None, File()] = None,
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
        content, mime = await uploads.read_validated(upload, allowed_types, max_bytes, field)
        file_bytes_map[field] = (content, mime)

    await _read_file(screen_image, _ALLOWED_IMAGE_TYPES, _MAX_SCREENSHOT_BYTES, "screen_image")
    await _read_file(face_image, _ALLOWED_IMAGE_TYPES, _MAX_SCREENSHOT_BYTES, "face_image")

    result = await candidate_service.flag_malpractice(
        db, submission_id, current_user["_id"], malpractice_type, file_bytes_map, description
    )
    if result.is_terminal:
        from app.components.scoring import scoring_tasks

        background_tasks.add_task(
            scoring_tasks.calculate_and_store_score,
            db,
            submission_id,
            result.current_round,
        )
    return success_response(SuccessMessages.MALPRACTICE_RECORDED, result)


@router.post(
    "/submission/{submission_id}/malpractice/{event_index}/media",
)
@limiter.limit("20/minute")
async def upload_malpractice_media(
    request: Request,
    submission_id: str,
    event_index: int,
    db: DB,
    current_user: CurrentUser,
    video_chunk: Annotated[UploadFile | None, File()] = None,
    audio_clip: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Phase-2 media upload: attaches video/audio clips to an existing malpractice event."""
    video_bytes: bytes | None = None
    video_ct = uploads.MIME_WEBM
    audio_bytes: bytes | None = None
    audio_ct = uploads.MIME_WEBM

    if video_chunk is not None:
        video_bytes, video_ct = await uploads.read_validated(
            video_chunk, _ALLOWED_VIDEO_TYPES, _MAX_VIDEO_BYTES, "video_chunk"
        )

    if audio_clip is not None:
        audio_bytes, audio_ct = await uploads.read_validated(
            audio_clip, _ALLOWED_AUDIO_TYPES, _MAX_AUDIO_BYTES, "audio_clip"
        )

    await candidate_service.upload_malpractice_media(
        db,
        submission_id,
        event_index,
        current_user["_id"],
        video_bytes,
        video_ct,
        audio_bytes,
        audio_ct,
    )
    return success_response(SuccessMessages.MALPRACTICE_MEDIA_UPLOADED)


@router.post(
    "/submission/{submission_id}/livekit-token",
    response_model=ApiResponse[LiveKitTokenResponse],
)
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
        return success_response(SuccessMessages.LIVEKIT_TOKEN_GENERATED, result)
    except NotFoundException:
        raise
    except Exception as exc:
        from app.common.exceptions import AppException
        from app.core.logging import logger

        logger.error(
            "LiveKit token generation failed: submission_id=%s error=%s", submission_id, exc
        )
        raise AppException(
            status_code=503, message="Live monitoring is temporarily unavailable"
        ) from exc


@router.get(
    "/submission/{submission_id}/session-state",
    response_model=ApiResponse[SessionStateResponse],
)
async def get_session_state(
    submission_id: str,
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.get_session_state(db, submission_id, current_user["_id"])
    return success_response(SuccessMessages.SESSION_STATE_RETRIEVED, result)


@router.get("/submission/status", response_model=ApiResponse[SubmissionStatusResponse])
async def get_submission_status(
    share_link: Annotated[str, Query()],
    db: DB,
    current_user: CurrentUser,
) -> dict:
    result = await candidate_service.get_submission_status(db, share_link, current_user["_id"])
    return success_response(SuccessMessages.SUBMISSION_STATUS_RETRIEVED, result)


@router.get("/live-interviews")
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
    workspace_ids = None if is_super_admin(current_user) else member_workspace_ids(current_user)
    result = await candidate_service.get_live_interviews(
        db, search, monitoring_type, sort_by, sort_order, page, page_size, workspace_ids
    )
    return success_response(SuccessMessages.LIVE_INTERVIEWS_RETRIEVED, result)
