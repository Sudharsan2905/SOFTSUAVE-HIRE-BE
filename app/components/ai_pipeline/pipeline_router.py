"""AI hiring pipeline endpoints (admin only, workspace scoped)."""

import json
import mimetypes
from typing import Annotated

from bson import ObjectId
from fastapi import Form, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError

from app.common.constants.app_constants import UserRole
from app.common.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.common.responses import success_response
from app.common.router import DefaultResponseRouter
from app.components.ai_pipeline import document_parser, file_storage, pipeline_service, report_service
from app.components.ai_pipeline.pipeline_schemas import CandidateInput, RetryRequest
from app.components.auth.auth_dependencies import AdminUser
from app.components.workspace import workspace_service
from app.core.config import settings
from app.core.dependencies import DB

router = DefaultResponseRouter()

_MIN_JD_CHARS = 20


def _is_super(user: dict) -> bool:
    return user.get("role") == UserRole.SUPER_ADMIN.value


def _check_workspace(user: dict, workspace_id: str) -> None:
    if not _is_super(user) and workspace_id not in [str(w) for w in user.get("workspace_ids", [])]:
        raise ForbiddenException("No access to this workspace")


async def _load_authorized(db: DB, user: dict, pipeline_id: str) -> dict:
    pipeline = await pipeline_service.get_pipeline_doc(db, pipeline_id)
    _check_workspace(user, str(pipeline["workspace_id"]))
    return pipeline


def _parse_candidates(raw: str) -> list[CandidateInput]:
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("candidates must be a JSON list")
        items = [CandidateInput.model_validate(c) for c in data]
    except (ValueError, ValidationError) as exc:
        raise ValidationException("Invalid 'candidates' JSON", str(exc)[:300]) from exc
    if not items:
        raise ValidationException("At least one candidate is required")
    if len(items) > settings.PIPELINE_MAX_CANDIDATES:
        raise ValidationException(f"At most {settings.PIPELINE_MAX_CANDIDATES} candidates per pipeline")
    return items


async def _read_upload(form: object, field: str, allowed: tuple[str, ...]) -> tuple[str, bytes] | None:
    upload = form.get(field)  # type: ignore[attr-defined]
    if upload is None or not hasattr(upload, "read") or not getattr(upload, "filename", ""):
        return None
    name = file_storage.safe_name(upload.filename)
    if not name.lower().endswith(allowed):
        raise ValidationException(f"{field}: unsupported file type (allowed: {', '.join(allowed)})")
    limit = settings.PIPELINE_MAX_FILE_MB * 1024 * 1024
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise ValidationException(f"{field}: file exceeds {settings.PIPELINE_MAX_FILE_MB} MB")
    if not data:
        raise ValidationException(f"{field}: file is empty")
    return name, data


@router.post("", status_code=201)
async def create_pipeline(
    request: Request,
    db: DB,
    current_user: AdminUser,
    jd_title: Annotated[str, Form()],
    jd_text: Annotated[str, Form()],
    workspace_id: Annotated[str, Form()],
    candidates: Annotated[str, Form()],
) -> dict:
    if not jd_title.strip() or len(jd_text.strip()) < _MIN_JD_CHARS:
        raise ValidationException(f"jd_title and jd_text (min {_MIN_JD_CHARS} chars) are required")
    if not ObjectId.is_valid(workspace_id):
        raise ValidationException("Invalid workspace_id")
    await workspace_service.get_workspace(
        db, workspace_id, current_user["role"], [str(w) for w in current_user.get("workspace_ids", [])]
    )
    items = _parse_candidates(candidates)
    form = await request.form()
    pid = ObjectId()
    prepared: list[dict] = []
    uploads: list[tuple[str, bytes]] = []
    for i, item in enumerate(items):
        resume = await _read_upload(form, f"resume_{i}", document_parser.ALLOWED_RESUME_EXT)
        photo = await _read_upload(form, f"photo_{i}", document_parser.ALLOWED_PHOTO_EXT)
        if not resume and not item.description.strip():
            raise ValidationException(f"Candidate {i + 1}: provide a resume or a description")
        cand = item.model_dump() | {"index": i}
        if resume:
            cand["resume_key"] = f"{pid}/{i}_resume_{resume[0]}"
            cand["resume_filename"] = resume[0]
            uploads.append((cand["resume_key"], resume[1]))
        if photo:
            cand["photo_key"] = f"{pid}/{i}_photo_{photo[0]}"
            uploads.append((cand["photo_key"], photo[1]))
        prepared.append(cand)
    for key, data in uploads:
        await file_storage.save(key, data)
    created = await pipeline_service.start_run(
        db, pipeline_id=pid, workspace_id=workspace_id, jd_title=jd_title, jd_text=jd_text,
        candidates=prepared, created_by=current_user["_id"],
    )
    return success_response("Pipeline started", {"id": created, "status": "queued"})


@router.get("")
async def list_pipelines(
    db: DB,
    current_user: AdminUser,
    workspace_id: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    if workspace_id:
        if not ObjectId.is_valid(workspace_id):
            raise ValidationException("Invalid workspace_id")
        _check_workspace(current_user, workspace_id)
    scope = None if _is_super(current_user) else [str(w) for w in current_user.get("workspace_ids", [])]
    data = await pipeline_service.list_runs(
        db, workspace_ids=scope, workspace_id=workspace_id, page=page, page_size=page_size
    )
    return success_response("Pipelines retrieved", data)


@router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str, db: DB, current_user: AdminUser) -> dict:
    await _load_authorized(db, current_user, pipeline_id)
    return success_response("Pipeline retrieved", await pipeline_service.get_run(db, pipeline_id))


@router.get("/{pipeline_id}/questions")
async def get_pipeline_questions(pipeline_id: str, db: DB, current_user: AdminUser) -> dict:
    await _load_authorized(db, current_user, pipeline_id)
    return success_response("Questions retrieved", await pipeline_service.get_questions(db, pipeline_id))


@router.post("/{pipeline_id}/invites/resend")
async def resend_all_invites(pipeline_id: str, db: DB, current_user: AdminUser) -> dict:
    await _load_authorized(db, current_user, pipeline_id)
    return success_response("Invites processed", await pipeline_service.resend_invites(db, pipeline_id))


@router.post("/{pipeline_id}/candidates/{candidate_id}/invite/resend")
async def resend_candidate_invite(
    pipeline_id: str, candidate_id: str, db: DB, current_user: AdminUser
) -> dict:
    await _load_authorized(db, current_user, pipeline_id)
    result = await pipeline_service.resend_invites(db, pipeline_id, candidate_id)
    return success_response("Invite processed", result)


@router.post("/{pipeline_id}/retry")
async def retry_pipeline(
    pipeline_id: str, db: DB, current_user: AdminUser, body: RetryRequest | None = None
) -> dict:
    await _load_authorized(db, current_user, pipeline_id)
    result = await pipeline_service.retry_step(db, pipeline_id, body.step if body else None)
    return success_response("Pipeline re-run started", result)


@router.post("/{pipeline_id}/candidates/{candidate_id}/report/resend")
async def resend_report(pipeline_id: str, candidate_id: str, db: DB, current_user: AdminUser) -> dict:
    await _load_authorized(db, current_user, pipeline_id)
    cand = await pipeline_service.get_candidate_doc(db, pipeline_id, candidate_id)
    if not await report_service.resend_report(db, cand):
        raise ValidationException("The report cannot be sent yet: exam not submitted or not fully evaluated")
    return success_response("Report sent", {"sent": True})


async def _file(db: DB, user: dict, pipeline_id: str, candidate_id: str, field: str) -> Response:
    await _load_authorized(db, user, pipeline_id)
    cand = await pipeline_service.get_candidate_doc(db, pipeline_id, candidate_id)
    key = cand.get(field)
    if not key:
        raise NotFoundException("File not found")
    data = await file_storage.read(key)
    return Response(content=data, media_type=mimetypes.guess_type(key)[0] or "application/octet-stream")


@router.get("/{pipeline_id}/candidates/{candidate_id}/resume", response_model=None)
async def get_resume(pipeline_id: str, candidate_id: str, db: DB, current_user: AdminUser) -> Response:
    return await _file(db, current_user, pipeline_id, candidate_id, "resume_key")


@router.get("/{pipeline_id}/candidates/{candidate_id}/photo", response_model=None)
async def get_photo(pipeline_id: str, candidate_id: str, db: DB, current_user: AdminUser) -> Response:
    return await _file(db, current_user, pipeline_id, candidate_id, "photo_key")
