import asyncio
import random
import time
from datetime import timedelta
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.common.constants.app_constants import QuestionType, SubmissionStatus
from app.common.constants.messages import ErrorMessages
from app.common.exceptions import (
    AppException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.common.response_models.candidate_responses import (
    AnswerSavedResponse,
    MalpracticeRecordResponse,
    SessionStateResponse,
    SubmissionStatusResponse,
)
from app.common.utils import (
    build_pagination_meta,
    decode_sharelink,
    paginate_query,
    safe_regex,
    serialize_doc,
    serialize_docs,
    utcnow,
)
from app.common.validators import is_valid_question_image_key
from app.components.candidate import timing
from app.components.storage import s3_service
from app.components.websocket.connection_manager import admin_manager, manager
from app.core.config import settings
from app.core.logging import logger

_DEFAULT_CONTENT_TYPE = "image/jpeg"
_ERR_ACCESS_RESTRICTED = "You are not authorized to access this assessment link."


# MongoDB pipeline operator constants
_MATCH = "$match"
_REGEX = "$regex"
_OPTIONS = "$options"

# Use SystemRandom (backed by os.urandom) for question shuffling
_rng = random.SystemRandom()

# Malpractice event type → monitoring field mapping
_SCREEN_BEHAVIORAL_EVENTS = {
    "TAB_SWITCH",
    "FULLSCREEN_EXIT",
    "SCREEN_SHARE_STOP",
    "DEVTOOLS_OPEN",
    "COPY_PASTE",
    "KEYBOARD_SHORTCUT",
}
_VIDEO_EVENTS = {"FACE_ABSENCE", "MULTIPLE_FACES", "EYE_DIRECTION"}
_AUDIO_EVENTS = {"AUDIO_VIOLATION", "SPEAKING", "BACKGROUND_NOISE"}


_PUBLIC_ASSESSMENT_HIDDEN_FIELDS = (
    "share_link",
    "restrict_candidate_access",
    "restriction_mode",
    "restricted_emails",
    "created_by",
    "pass_percentage",  # results are admin-only; candidates never see the pass mark
)
_MAX_ANSWER_CHARS = 20_000
_MAX_ANSWER_ITEMS = 50


async def _resolve_assessment_and_share(
    db: AsyncIOMotorDatabase, share_link: str
) -> tuple[dict | None, dict | None]:
    """Resolve (assessment, share_doc) for a candidate share link.

    A signed link that carries a nonce ("n") is only valid while its assessment_shares
    document is active — revoking the share must stop it working, so it never falls back
    to decoding the signature alone.
    """
    share_doc = await db.assessment_shares.find_one({"share_link": share_link, "is_active": True})
    if share_doc:
        assessment = await db.assessments.find_one(
            {"_id": share_doc["assessment_id"], "is_active": True}
        )
        return assessment, share_doc

    try:
        decoded = decode_sharelink(share_link)
    except Exception:
        # Legacy: plain share_link field on the assessment document.
        return await db.assessments.find_one({"share_link": share_link, "is_active": True}), None

    if "n" in decoded:
        return None, None  # nonce link whose share was revoked / never existed
    try:
        assessment = await db.assessments.find_one(
            {"_id": ObjectId(decoded["a"]), "is_active": True}
        )
    except Exception:
        assessment = None
    return assessment, None


def _enforce_share_window(share_doc: dict | None) -> None:
    """Reject a *new* attempt outside the share link's start/end window."""
    if not share_doc:
        return
    now = utcnow()
    start = timing.aware(share_doc.get("start_time"))
    end = timing.aware(share_doc.get("end_time"))
    if start and now < start:
        raise ForbiddenException("This assessment has not started yet.")
    if end and now > end:
        raise ForbiddenException("This assessment link has expired.")


async def get_candidate_assessment(db: AsyncIOMotorDatabase, share_link: str) -> dict:
    """Return assessment metadata for a candidate via share link, without internal question IDs.

    Also patches monitoring_overrides from the matching assessment_shares document so the
    frontend receives the effective monitoring config for this specific share link.

    Raises:
        NotFoundException: If the share link is invalid.
    """
    doc, share_doc = await _resolve_assessment_and_share(db, share_link)

    if not doc:
        raise NotFoundException(ErrorMessages.ASSESSMENT_NOT_FOUND)

    safe = serialize_doc(doc)
    for field in _PUBLIC_ASSESSMENT_HIDDEN_FIELDS:
        safe.pop(field, None)
    for r in safe.get("rounds", []):
        r.pop("question_ids", None)

    if share_doc:
        overrides = share_doc.get("monitoring_overrides") or {}
        non_null_overrides = {k: v for k, v in overrides.items() if v is not None}
        if non_null_overrides:
            safe["monitoring_config"] = {
                **(safe.get("monitoring_config") or {}),
                **non_null_overrides,
            }

    return safe


async def _handle_existing_submission(db: AsyncIOMotorDatabase, existing: dict) -> dict:
    """Resume or raise for an existing submission.

    ON_HOLD sessions are returned as-is so the candidate's interview page can
    display the "Awaiting admin resume" overlay via WebSocket.

    Raises:
        ForbiddenException: If the submission is in a terminal state.
    """
    status = existing.get("status")
    if status in [
        SubmissionStatus.COMPLETED,
        SubmissionStatus.TERMINATED,
        SubmissionStatus.MALPRACTICE,
    ]:
        raise ForbiddenException(
            "You have already completed this assessment. Please contact admin for re-access."
        )
    # ON_HOLD and IN_PROGRESS are both returnable — the WS will tell candidate the real state
    if status in [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]:
        return _candidate_view(serialize_doc(existing))

    now = utcnow()
    set_fields: dict = {
        "status": SubmissionStatus.IN_PROGRESS,
        "started_at": now,
        "updated_at": now,
    }
    rd = timing.current_round_doc(existing)
    if rd is not None:
        set_fields.update(timing.provisional_round_fields(now, rd))
    updated = await db.assessment_submissions.find_one_and_update(
        {"_id": existing["_id"]},
        {"$set": set_fields},
        return_document=ReturnDocument.AFTER,
    )
    return _candidate_view(serialize_doc(updated))


def _answer_key_snapshot(q: dict) -> dict:
    """Grading data copied from the live question document (options with ``is_correct``,
    reference answer, question text)."""
    return {
        "question_text": q.get("question_text", ""),
        "correct_answer": q.get("correct_answer"),
        "options": [
            {
                "id": str(o.get("id", o.get("_id", ""))),
                "text": o.get("text", ""),
                "is_correct": bool(o.get("is_correct")),
            }
            for o in q.get("options") or []
        ],
    }


def _candidate_view(sub: dict) -> dict:
    """Remove server-only grading data from a serialised submission before it is returned
    to the candidate."""
    for rd in sub.get("rounds_data") or []:
        for q in rd.get("questions") or []:
            q.pop("answer_key", None)
    return sub


def _sanitize_question(q: dict) -> dict:
    """Return only candidate-facing fields; strip answers and shuffle MCQ options."""
    sq = serialize_doc(q)
    q_type = sq.get("question_type", "essay")
    options: list = []
    if q_type in (QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI):
        options = [{k: v for k, v in o.items() if k != "is_correct"} for o in sq.get("options", [])]
        _rng.shuffle(options)
    return {
        "id": sq.get("id") or sq.get("_id", ""),
        "type": "mcq_multiple" if q_type == QuestionType.MCQ_MULTI else q_type,
        "text": sq.get("question_text", ""),
        "complexity": sq.get("complexity"),
        "image_key": sq.get("image_key"),
        "options": options,
    }


async def _resolve_question_image_url(image_key: str | None) -> str | None:
    """Return a presigned URL for an S3 image key, or None if absent/on error."""
    if not image_key or not is_valid_question_image_key(image_key):
        return None
    try:
        return await s3_service.get_presigned_url(image_key)
    except Exception:
        return None


async def _resolve_questions_images(questions: list[dict]) -> list[dict]:
    """Return a copy of candidate-facing questions with image_key replaced by a presigned
    image_url on the question and each option. URLs are resolved concurrently."""

    async def _one(q: dict) -> dict:
        qd = dict(q)
        qd.pop("answer_key", None)  # never sent to the candidate
        option_dicts = [dict(o) for o in qd.get("options") or []]
        urls = await asyncio.gather(
            _resolve_question_image_url(qd.pop("image_key", None)),
            *(_resolve_question_image_url(od.pop("image_key", None)) for od in option_dicts),
        )
        qd["image_url"] = urls[0]
        for od, url in zip(option_dicts, urls[1:], strict=True):
            od["image_url"] = url
        qd["options"] = option_dicts
        return qd

    return list(await asyncio.gather(*(_one(q) for q in questions)))


async def _build_round_data(
    db: AsyncIOMotorDatabase,
    round_cfg: dict,
    override_question_ids: list | None = None,
) -> dict:
    """Sample and sanitize questions for a single assessment round.

    If override_question_ids is provided (from a candidate schedule) those IDs are
    used directly instead of randomly sampling from the assessment pool.
    """
    required = round_cfg["question_count"]
    if override_question_ids:
        selected_ids = override_question_ids[: max(required, len(override_question_ids))]
    else:
        question_ids = round_cfg.get("question_ids", [])
        selected_ids = (
            _rng.sample(question_ids, min(required, len(question_ids)))
            if len(question_ids) >= required
            else question_ids
        )
    questions_raw = await db.questions.find({"_id": {"$in": selected_ids}}).to_list(
        len(selected_ids)
    )
    safe_questions = []
    for q in questions_raw:
        sq = _sanitize_question(q)
        # Snapshot of the grading data, frozen at exam start: editing or deleting the live
        # question afterwards must not change how this attempt is scored. Server-side only —
        # stripped from every candidate-facing payload (see _candidate_view).
        sq["answer_key"] = _answer_key_snapshot(q)
        safe_questions.append(sq)
    _rng.shuffle(safe_questions)
    return {
        "round_number": round_cfg["round_number"],
        "question_count": required,
        "max_duration_minutes": round_cfg["max_duration_minutes"],
        "questions": safe_questions,
        "answers": {},
        "completed": False,
        "started_at": None,
    }


async def _check_candidate_access_restriction(
    db: AsyncIOMotorDatabase,
    share_doc: dict,
    candidate_id: str,
) -> None:
    """Raise ForbiddenException if the share link's candidate restriction blocks this candidate.

    Does nothing when restriction is disabled or share_doc has no restriction config.
    """
    if not share_doc.get("restrict_candidate_access"):
        return

    restriction_mode = share_doc.get("restriction_mode")
    restricted_emails: list[str] = share_doc.get("restricted_emails") or []

    candidate_doc = await db.users.find_one({"_id": ObjectId(candidate_id)})
    candidate_email = ((candidate_doc.get("email") or "") if candidate_doc else "").strip().lower()

    blocked = (restriction_mode == "INCLUDE" and candidate_email not in restricted_emails) or (
        restriction_mode == "EXCLUDE" and candidate_email in restricted_emails
    )
    if blocked:
        raise ForbiddenException(_ERR_ACCESS_RESTRICTED)


async def start_assessment(db: AsyncIOMotorDatabase, share_link: str, candidate_id: str) -> dict:
    """Start or resume a candidate's assessment submission.

    - Returns existing submission if already IN_PROGRESS or ON_HOLD.
    - Transitions PENDING → IN_PROGRESS without re-sampling questions.
    - Creates a new submission on first call, resolving monitoring_details from the
      assessment's monitoring_config merged with the assessment_shares document's
      monitoring_overrides and start/end window.

    Raises:
        NotFoundException: If the assessment share link is invalid.
        ForbiddenException: If the submission is already COMPLETED or MALPRACTICE.
    """
    assessment, share_doc = await _resolve_assessment_and_share(db, share_link)

    if not assessment:
        raise NotFoundException(ErrorMessages.ASSESSMENT_NOT_FOUND)

    # ── Candidate access restriction check ─────────────────────────────────
    if share_doc:
        await _check_candidate_access_restriction(db, share_doc, candidate_id)
    else:
        await _check_candidate_access_restriction(db, assessment, candidate_id)

    existing = await db.assessment_submissions.find_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
        }
    )
    if existing:
        return await _handle_existing_submission(db, existing)

    # A brand-new attempt must fall inside the share link's validity window.
    _enforce_share_window(share_doc)

    rounds_data = list(
        await asyncio.gather(
            *(_build_round_data(db, round_cfg) for round_cfg in assessment.get("rounds", []))
        )
    )

    monitoring_details = _build_monitoring_details(assessment, share_doc)

    now = utcnow()
    first_round = next((rd for rd in rounds_data if rd.get("round_number") == 1), None)
    timer_fields: dict = {}
    if first_round is not None:
        first_round["started_at"] = now
        timer_fields = timing.provisional_round_fields(now, first_round)

    # New participants join the assessment's newest batch (Batch-1 until another is created).
    from app.components.assessment.assessment_service import ensure_default_batch

    await ensure_default_batch(db, assessment)

    submission = {
        "assessment_id": assessment["_id"],
        "candidate_id": ObjectId(candidate_id),
        "batch": assessment["batch"][-1]["bId"],
        "share_id": ObjectId(share_doc["_id"]) if share_doc else None,
        "monitoring_details": monitoring_details,
        "status": SubmissionStatus.IN_PROGRESS,
        "rounds_data": rounds_data,
        "current_round": 1,
        "workspace_id": assessment.get("workspace_id"),
        **timer_fields,
        "score": 0,
        "percentage": 0.0,
        "screenshots": [],
        "malpractice_count": 0,
        "malpractice_events": [],
        "reaccess_count": 0,
        "is_active": True,
        "started_at": now,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.assessment_submissions.insert_one(submission)
    submission["_id"] = result.inserted_id
    logger.info(
        f"Assessment started: candidate_id={candidate_id} share_link={share_link} "
        f"submission_id={result.inserted_id}"
    )
    return _candidate_view(serialize_doc(submission))


def _build_monitoring_details(assessment: dict | None, share_doc: dict | None) -> dict:
    """Merge assessment-level monitoring defaults with the share's candidate overrides,
    plus the share link's validity window, into a single snapshot to persist on the
    submission at creation time.

    Deliberately excludes restrict_candidate_access (and other share access-control
    fields) — those govern link access, not the monitoring/proctoring config.
    """
    base_monitoring: dict = (assessment.get("monitoring_config") or {}) if assessment else {}
    candidate_overrides: dict = (share_doc.get("monitoring_overrides") or {}) if share_doc else {}
    effective: dict = {
        **base_monitoring,
        **{k: v for k, v in candidate_overrides.items() if v is not None},
    }

    return {
        "tab_monitoring": effective.get("tab_monitoring", False),
        "audio_monitoring": effective.get("audio_monitoring", False),
        "video_monitoring": effective.get("video_monitoring", False),
        "screenshot_enabled": effective.get("screenshot_enabled", False),
        "screenshot_mode": effective.get("screenshot_mode", "time_interval"),
        "screenshot_interval_seconds": effective.get("screenshot_interval_seconds"),
        "screenshot_count": effective.get("screenshot_count"),
        "start_time": share_doc.get("start_time") if share_doc else None,
        "end_time": share_doc.get("end_time") if share_doc else None,
    }


async def start_interview(
    db: AsyncIOMotorDatabase, submission_id: str, candidate_id: str
) -> dict[str, int]:
    """Start the server-side timer for the current round (idempotent).

    Returns ``{"remaining_seconds": n}`` so the browser can sync its countdown to the
    server clock the moment the round begins.

    Called from the interview screen after monitoring requirements are satisfied.  The
    timer starts exactly once per round: repeat calls (page reload, reconnect) never
    reset the deadline.

    Raises:
        NotFoundException: If no active in-progress submission exists.
    """
    sub = await db.assessment_submissions.find_one(
        {
            "_id": ObjectId(submission_id),
            "candidate_id": ObjectId(candidate_id),
            "status": {"$in": [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]},
        },
        {
            "current_round": 1,
            "round_timer_started": 1,
            "current_deadline_at": 1,
            "current_round_seconds": 1,
            "status": 1,
            "paused_at": 1,
            "rounds_data.round_number": 1,
            "rounds_data.max_duration_minutes": 1,
            "rounds_data.started_at": 1,
        },
    )
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)

    if timing.timer_started(sub):
        return {"remaining_seconds": timing.remaining_seconds(sub)}

    current = sub.get("current_round", 1)
    now = utcnow()
    seconds = timing.round_seconds(timing.current_round_doc(sub))
    set_fields: dict = {
        "rounds_data.$[rd].started_at": now,
        "rounds_data.$[rd].deadline_at": now + timedelta(seconds=seconds),
        "updated_at": now,
        **timing.started_round_fields(now, seconds),
    }
    if current == 1:
        set_fields["started_at"] = now

    await db.assessment_submissions.update_one(
        {"_id": sub["_id"], "round_timer_started": {"$ne": True}},
        {"$set": set_fields},
        array_filters=[{"rd.round_number": current}],
    )
    invalidate_answer_ctx(submission_id)
    return {"remaining_seconds": seconds}


async def get_submission_status(
    db: AsyncIOMotorDatabase, share_link: str, candidate_id: str
) -> SubmissionStatusResponse | None:
    """Return the current submission status for a candidate + assessment pair.

    Uses the share link to resolve the assessment, then looks up the submission
    by the (assessment_id, candidate_id) unique index.  Returns None when no
    submission exists yet (assessment not yet started by this candidate).

    Raises:
        NotFoundException: If the share link does not resolve to any assessment.
    """
    assessment = None
    try:
        decoded = decode_sharelink(share_link)
        assessment = await db.assessments.find_one(
            {"_id": ObjectId(decoded["a"]), "is_active": True}
        )
    except Exception:
        assessment = await db.assessments.find_one({"share_link": share_link, "is_active": True})

    if not assessment:
        raise NotFoundException(ErrorMessages.ASSESSMENT_NOT_FOUND)

    sub = await db.assessment_submissions.find_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
        }
    )
    if not sub:
        return None

    return SubmissionStatusResponse(
        submission_id=str(sub["_id"]),
        status=str(sub.get("status", "")),
        assessment_id=str(sub["assessment_id"]),
        candidate_id=str(sub["candidate_id"]),
        current_round=sub.get("current_round", 1),
        completed_at=sub["completed_at"].isoformat() if sub.get("completed_at") else None,
        paused_at=sub["paused_at"].isoformat() if sub.get("paused_at") else None,
        malpractice_count=sub.get("malpractice_count", 0),
    )


async def get_current_round(
    db: AsyncIOMotorDatabase, submission_id: str, candidate_id: str
) -> dict:
    """Return the questions and metadata for the candidate's current round.

    Correct answers and is_correct flags are never included in the response.
    ``remaining_seconds`` is always computed by the server from the round deadline.

    Raises:
        NotFoundException: If the submission or round index is not found.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        raise NotFoundException("Submission not found")

    sub_status = sub.get("status")
    if sub_status == SubmissionStatus.COMPLETED:
        logger.warning(
            f"Security: blocked round access on completed submission "
            f"submission_id={submission_id} candidate_id={candidate_id}"
        )
        raise ForbiddenException("This assessment has already been completed.")
    if sub_status == SubmissionStatus.TERMINATED:
        raise ForbiddenException("This session has been terminated by an administrator.")
    if sub_status == SubmissionStatus.MALPRACTICE:
        raise ForbiddenException("This session has ended due to malpractice.")
    # ON_HOLD: allow reading round data so the frontend can restore UI; it shows the
    # ON_HOLD overlay via WebSocket rather than blocking entirely.

    current = sub.get("current_round", 1)
    idx = current - 1
    rounds_data = sub.get("rounds_data", [])

    if idx >= len(rounds_data):
        raise NotFoundException("Round not found")

    rd = rounds_data[idx]

    # Monitoring config was fully resolved (assessment defaults + share overrides) at
    # start_assessment time and persisted on the submission.
    effective: dict = sub.get("monitoring_details") or {}

    return {
        "round": {
            "round_number": current,
            "questions": await _resolve_questions_images(rd.get("questions", [])),
            "max_duration_minutes": timing.round_seconds(rd) // 60,
        },
        "tab_monitoring": effective.get("tab_monitoring", False),
        "audio_monitoring": effective.get("audio_monitoring", False),
        "video_monitoring": effective.get("video_monitoring", False),
        "screenshot_enabled": effective.get("screenshot_enabled", False),
        "screenshot_mode": effective.get("screenshot_mode", "time_interval"),
        "screenshot_interval_seconds": effective.get("screenshot_interval_seconds"),
        "screenshot_count": effective.get("screenshot_count"),
        # Server-owned timer: the single source of truth for the countdown.
        "remaining_seconds": timing.remaining_seconds(sub),
        "current_question_idx": sub.get("current_question_idx", 0),
        # The candidate's own saved answers, so a reload restores their progress.
        "answers": rd.get("answers") or {},
        "session_status": sub_status if sub_status else "in_progress",
    }


def _validate_answer_payload(answer: Any) -> None:
    if isinstance(answer, str):
        if len(answer) > _MAX_ANSWER_CHARS:
            raise ValidationException("Answer is too long")
    elif isinstance(answer, list):
        if len(answer) > _MAX_ANSWER_ITEMS or any(
            not isinstance(a, str) or len(a) > _MAX_ANSWER_CHARS for a in answer
        ):
            raise ValidationException("Invalid answer")
    else:
        raise ValidationException("Invalid answer")


MAX_SCREENSHOTS_PER_SESSION = 2000
_ANSWER_CTX_TTL_SECONDS = 20.0
_ANSWER_CTX_MAX = 20_000
# (monotonic stored_at, context) per "submission:candidate".  Holds only what is needed to
# validate an answer so the hot path avoids re-reading the large submission document.
_answer_ctx: dict[str, tuple[float, dict]] = {}


def invalidate_answer_ctx(submission_id: str) -> None:
    """Forget cached answer contexts for a submission (round/deadline/status changed)."""
    # list() snapshot: the reaper thread may call this while the API loop mutates the dict
    for key in [k for k in list(_answer_ctx) if k.startswith(f"{submission_id}:")]:
        _answer_ctx.pop(key, None)


async def _load_answer_ctx(db: AsyncIOMotorDatabase, submission_id: str, candidate_id: str) -> dict:
    key = f"{submission_id}:{candidate_id}"
    hit = _answer_ctx.get(key)
    if hit and time.monotonic() - hit[0] < _ANSWER_CTX_TTL_SECONDS:
        return hit[1]

    sub = await db.assessment_submissions.find_one(
        {
            "_id": ObjectId(submission_id),
            "candidate_id": ObjectId(candidate_id),
            "status": {"$in": [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]},
        },
        {
            "current_round": 1,
            "status": 1,
            "paused_at": 1,
            "round_timer_started": 1,
            "current_deadline_at": 1,
            "current_round_seconds": 1,
            "rounds_data.round_number": 1,
            "rounds_data.max_duration_minutes": 1,
            "rounds_data.started_at": 1,
            "rounds_data.questions.id": 1,
            "rounds_data.questions._id": 1,
        },
    )
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)

    current = sub.get("current_round", 1)
    rd = timing.current_round_doc(sub)
    idx = next(
        (i for i, r in enumerate(sub.get("rounds_data", [])) if r.get("round_number") == current),
        current - 1,
    )
    ctx = {
        "oid": sub["_id"],
        "current_round": current,
        "idx": idx,
        "qids": {str(q.get("id") or q.get("_id")) for q in (rd or {}).get("questions", [])},
        "timing_sub": {
            k: sub.get(k)
            for k in (
                "status",
                "paused_at",
                "round_timer_started",
                "current_deadline_at",
                "current_round_seconds",
                "current_round",
                "rounds_data",
            )
        },
    }
    if len(_answer_ctx) >= _ANSWER_CTX_MAX:
        _answer_ctx.clear()
    _answer_ctx[key] = (time.monotonic(), ctx)
    return ctx


async def submit_answer(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str,
    question_id: str,
    answer: Any,
) -> AnswerSavedResponse:
    """Persist a candidate's answer for a question in the current round.

    Rejects answers for questions outside the current round and answers sent after the
    server-side deadline (plus a small network grace).  The per-submission context needed
    for those checks is cached briefly, and the write itself is guarded on status and
    round, so a stale cache can never write into a finished or advanced session.

    Raises:
        NotFoundException: If no active in-progress submission exists.
        ForbiddenException: If the round time is over.
    """
    _validate_answer_payload(answer)
    ctx = await _load_answer_ctx(db, submission_id, candidate_id)

    if question_id not in ctx["qids"] or timing.is_expired(
        ctx["timing_sub"], settings.SUBMIT_GRACE_SECONDS
    ):
        # The cache is per process: with several workers the round/deadline may have moved
        # on another one. Re-read once before rejecting.
        invalidate_answer_ctx(submission_id)
        ctx = await _load_answer_ctx(db, submission_id, candidate_id)
    if timing.is_expired(ctx["timing_sub"], settings.SUBMIT_GRACE_SECONDS):
        raise ForbiddenException("Time is over for this round.")
    if question_id not in ctx["qids"]:
        raise ValidationException("Question does not belong to the current round")

    result = await db.assessment_submissions.update_one(
        {
            "_id": ctx["oid"],
            "status": {"$in": [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]},
            "current_round": ctx["current_round"],
        },
        {
            "$set": {
                f"rounds_data.{ctx['idx']}.answers.{question_id}": answer,
                "updated_at": utcnow(),
            }
        },
    )
    if result.matched_count == 0:  # round advanced or session ended since the cache was filled
        invalidate_answer_ctx(submission_id)
        fresh = await _load_answer_ctx(db, submission_id, candidate_id)  # NotFound if ended
        if fresh["current_round"] == ctx["current_round"] or question_id not in fresh["qids"]:
            raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)
        result = await db.assessment_submissions.update_one(
            {
                "_id": fresh["oid"],
                "status": {"$in": [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]},
                "current_round": fresh["current_round"],
            },
            {
                "$set": {
                    f"rounds_data.{fresh['idx']}.answers.{question_id}": answer,
                    "updated_at": utcnow(),
                }
            },
        )
        if result.matched_count == 0:
            raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)
    return AnswerSavedResponse(saved=True)


async def finish_round(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str,
    expected_round: int | None = None,
) -> dict:
    """Mark the current round complete and advance or finalise the assessment.

    Does NOT score the round — scoring is handled by a background task after this returns.
    Idempotent: finishing an already-completed submission returns ``completed=True`` with
    ``already_completed=True`` (the caller must not re-score).  When the client states which
    round it is finishing (``expected_round``) and that round was already submitted — e.g. the
    timer expired and the candidate also clicked Submit — the call is a no-op instead of
    skipping the next round.  Terminal states other than COMPLETED can never be revived.

    Returns:
        {'completed': True, 'finished_round': int} if all rounds are done,
        {'completed': False, 'next_round': int, 'finished_round': int} otherwise.

    Raises:
        NotFoundException: If no matching submission or round is found.
        ForbiddenException: If the submission is not IN_PROGRESS.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)},
        {
            "status": 1,
            "current_round": 1,
            "rounds_data.round_number": 1,
            "rounds_data.max_duration_minutes": 1,
        },
    )
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)

    current = sub.get("current_round", 1)
    status = sub.get("status")
    if status == SubmissionStatus.COMPLETED:
        return {"completed": True, "finished_round": current, "already_completed": True}
    if status != SubmissionStatus.IN_PROGRESS:
        raise ForbiddenException(f"Cannot submit a session with status '{status}'.")
    if expected_round is not None and expected_round != current:
        return {
            "completed": False,
            "next_round": current,
            "finished_round": expected_round,
            "already_completed": True,
        }

    rounds = sub.get("rounds_data", [])
    current_rd = timing.current_round_doc(sub)
    if not current_rd:
        raise NotFoundException("Round not found")

    total_rounds = len(rounds)
    now = utcnow()
    is_last_round = current >= total_rounds

    set_fields: dict = {
        "rounds_data.$[rd].completed": True,
        "rounds_data.$[rd].completed_at": now,
        "updated_at": now,
    }
    array_filters: list[dict] = [{"rd.round_number": current}]

    if is_last_round:
        set_fields["status"] = SubmissionStatus.COMPLETED
        set_fields["completed_at"] = now
    else:
        next_rd = next((r for r in rounds if r.get("round_number") == current + 1), None)
        set_fields["current_round"] = current + 1
        set_fields["current_question_idx"] = 0
        set_fields["rounds_data.$[nxt].started_at"] = now
        array_filters.append({"nxt.round_number": current + 1})
        if next_rd is not None:
            set_fields.update(timing.provisional_round_fields(now, next_rd))

    # Atomic transition: only an IN_PROGRESS submission still on `current` can advance,
    # so a replayed / concurrent finish cannot double-advance or revive a terminal state.
    result = await db.assessment_submissions.update_one(
        {"_id": sub["_id"], "status": SubmissionStatus.IN_PROGRESS, "current_round": current},
        {"$set": set_fields, "$unset": {"remaining_seconds": ""}},
        array_filters=array_filters,
    )
    invalidate_answer_ctx(submission_id)
    if result.matched_count == 0:
        raise ForbiddenException("This round has already been submitted.")

    if is_last_round:
        logger.info(
            "Assessment completed: submission_id=%s candidate_id=%s", submission_id, candidate_id
        )
        return {"completed": True, "finished_round": current}

    logger.info(
        "Round %d finished: submission_id=%s → advancing to round %d",
        current,
        submission_id,
        current + 1,
    )
    return {"completed": False, "next_round": current + 1, "finished_round": current}


async def save_screenshot(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str,
    file_bytes: bytes,
    content_type: str = _DEFAULT_CONTENT_TYPE,
) -> None:
    """Upload a screenshot to S3 and record its key in the submission document.

    Silently no-ops if the submission is not found or is no longer active.
    """
    sub = await db.assessment_submissions.find_one(
        {
            "_id": ObjectId(submission_id),
            "candidate_id": ObjectId(candidate_id),
            "status": {"$in": [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]},
            # Per-session cap: once slot N exists the session is full — drop further uploads.
            f"screenshots.{MAX_SCREENSHOTS_PER_SESSION - 1}": {"$exists": False},
        },
        {"current_round": 1},
    )
    if not sub:
        return

    round_number = sub.get("current_round", 1)
    timestamp = utcnow()

    s3_key = s3_service.make_screenshot_key(submission_id, round_number, timestamp.isoformat())
    await s3_service.upload(file_bytes, s3_key, content_type)

    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {
            "$push": {
                "screenshots": {
                    "s3_key": s3_key,
                    "round": round_number,
                    "taken_at": timestamp,
                }
            }
        },
    )
    logger.info(
        f"Screenshot uploaded: submission_id={submission_id} round={round_number} s3_key={s3_key}"
    )


def _is_malpractice_event_enabled(malpractice_type: str, effective_monitoring: dict) -> bool:
    """Return False if the event type is gated behind a disabled monitoring flag.

    VIDEO and AUDIO events require their respective monitoring flags.
    Screen/behavioral events are always allowed through.
    """
    mtype_upper = malpractice_type.upper()
    if mtype_upper in _VIDEO_EVENTS:
        return bool(effective_monitoring.get("video_monitoring", False))
    if mtype_upper in _AUDIO_EVENTS:
        return bool(effective_monitoring.get("audio_monitoring", False))
    # Screen/behavioral events have no explicit gate
    return True


async def _upload_evidence_files(
    submission_id: str,
    round_number: int,
    malpractice_type: str,
    now: Any,
    effective_monitoring: dict,
    file_bytes_map: dict,
) -> dict:
    """Upload applicable evidence files to S3 (concurrently) and return their keys.

    Keys in the returned dict: screen_image_s3_key, face_image_s3_key,
    screen_video_s3_key, audio_clip_s3_key.  Each is None when not uploaded.
    """

    async def _upload(map_key: str, fallback_ct: str, evidence_type: str) -> str | None:
        """Upload one evidence file (if present) and return its S3 key, else None."""
        data = file_bytes_map.get(map_key)
        if data is None:
            return None
        file_bytes, content_type = (
            (data[0], data[1] or fallback_ct) if isinstance(data, tuple) else (data, fallback_ct)
        )
        key = s3_service.make_evidence_key(
            submission_id, round_number, malpractice_type, evidence_type, now.isoformat()
        )
        await s3_service.upload(file_bytes, key, content_type)
        return key

    async def _skip() -> None:
        return None

    video = bool(effective_monitoring.get("video_monitoring", False))
    screen_image, face_image, screen_video, audio_clip = await asyncio.gather(
        _upload("screen_image", _DEFAULT_CONTENT_TYPE, "screen_image")
        if effective_monitoring.get("screenshot_enabled", False)
        else _skip(),
        _upload("face_image", _DEFAULT_CONTENT_TYPE, "face_image") if video else _skip(),
        _upload("video_chunk", "video/webm", "video_chunk") if video else _skip(),
        _upload("audio_clip", "audio/webm", "audio_clip")
        if effective_monitoring.get("audio_monitoring", False)
        else _skip(),
    )
    return {
        "screen_image_s3_key": screen_image,
        "face_image_s3_key": face_image,
        "screen_video_s3_key": screen_video,
        "audio_clip_s3_key": audio_clip,
    }


async def _record_malpractice_event(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    event_data: dict,
    now: Any,
) -> dict | None:
    """Atomically bump the strike counter and append the event.

    ``$inc`` + ``$push`` in a single update means concurrent violations can neither
    under-count strikes nor collide on ``event_index`` (== new_count - 1).  Returns the
    updated counters, or None if the submission is no longer active.
    """
    updated: dict | None = await db.assessment_submissions.find_one_and_update(
        {
            "_id": ObjectId(submission_id),
            "status": {"$in": [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]},
        },
        {
            "$inc": {"malpractice_count": 1},
            "$push": {"malpractice_events": event_data},
            "$set": {"updated_at": now},
        },
        projection={"malpractice_count": 1, "current_round": 1},
        return_document=ReturnDocument.AFTER,
    )
    return updated


async def _finalise_malpractice(db: AsyncIOMotorDatabase, submission_id: str, now: Any) -> None:
    """Terminate the session once the strike limit is reached and notify the candidate."""
    invalidate_answer_ctx(submission_id)
    await db.assessment_submissions.update_one(
        {"_id": ObjectId(submission_id)},
        {
            "$set": {
                "status": SubmissionStatus.MALPRACTICE,
                "completed_at": now,
                "updated_at": now,
            }
        },
    )
    await manager.send_json(submission_id, {"type": "terminated", "reason": "malpractice"})


async def _broadcast_malpractice(
    submission_id: str,
    event_data: dict,
    new_count: int,
    is_terminal: bool,
    now: Any,
    workspace_id: str,
) -> None:
    """Broadcast the malpractice event to admins monitoring this workspace."""
    await admin_manager.broadcast_event(
        {
            "type": "malpractice_event",
            "submission_id": submission_id,
            "event_type": event_data.get("type"),
            "round": event_data.get("round"),
            "malpractice_count": new_count,
            "is_terminal": is_terminal,
            "screen_image_url": event_data.get("screen_image_url"),
            "timestamp": now.isoformat() if hasattr(now, "isoformat") else str(now),
        },
        workspace_id,
    )


async def flag_malpractice(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str,
    malpractice_type: str,
    file_bytes_map: dict | None = None,
    description: str | None = None,
) -> MalpracticeRecordResponse:
    """Record a malpractice event using the 3-strike system.

    Accepts an optional file_bytes_map with keys: screen_image, face_image,
    video_chunk, audio_clip.  Files are conditionally uploaded to S3 based on
    the effective monitoring configuration.

    On the 3rd strike (malpractice_count >= MAX_MALPRACTICE_COUNT) the submission
    is terminated and a WebSocket event is pushed to the candidate.  Events for a
    submission that is no longer active are ignored (a finished or terminated session
    can't be flipped to MALPRACTICE).

    Raises:
        NotFoundException: If no matching submission exists.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)},
        {
            "status": 1,
            "current_round": 1,
            "malpractice_count": 1,
            "monitoring_details": 1,
            "workspace_id": 1,
            "assessment_id": 1,
        },
    )
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)

    round_number = sub.get("current_round", 1)
    current_count = sub.get("malpractice_count", 0)
    ignored = MalpracticeRecordResponse(
        malpractice_count=current_count, is_terminal=False, current_round=round_number
    )
    if sub.get("status") not in (SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD):
        return ignored

    # Already fully resolved (assessment defaults + share overrides) at start_assessment time.
    effective_monitoring: dict = sub.get("monitoring_details") or {}
    if not _is_malpractice_event_enabled(malpractice_type, effective_monitoring):
        return ignored

    workspace_id = str(sub["workspace_id"]) if sub.get("workspace_id") else ""
    if not workspace_id:  # legacy submission created before workspace_id was denormalised
        assessment = await db.assessments.find_one(
            {"_id": sub["assessment_id"]}, {"workspace_id": 1}
        )
        workspace_id = str(assessment["workspace_id"]) if assessment else ""

    now = utcnow()
    s3_keys = await _upload_evidence_files(
        submission_id,
        round_number,
        malpractice_type,
        now,
        effective_monitoring,
        file_bytes_map or {},
    )
    event_data: dict = {
        "type": malpractice_type,
        "description": description or "",
        "timestamp": now,
        "round": round_number,
        # Predicted from the count we read; the authoritative terminal decision below uses
        # the atomically-incremented counter.
        "is_terminal": current_count + 1 >= settings.MAX_MALPRACTICE_COUNT,
        **s3_keys,
    }

    updated = await _record_malpractice_event(db, submission_id, event_data, now)
    if updated is None:  # became inactive while evidence was uploading
        return ignored

    new_count = updated.get("malpractice_count", current_count + 1)
    event_index = new_count - 1
    is_terminal = new_count >= settings.MAX_MALPRACTICE_COUNT
    if is_terminal:
        await _finalise_malpractice(db, submission_id, now)
        event_data["is_terminal"] = True
        logger.warning(
            f"Malpractice terminal: submission_id={submission_id} type={malpractice_type} "
            f"count={new_count}"
        )
    else:
        logger.info(
            f"Malpractice flagged: submission_id={submission_id} type={malpractice_type} "
            f"count={new_count}"
        )

    await _broadcast_malpractice(
        submission_id, event_data, new_count, is_terminal, now, workspace_id
    )
    return MalpracticeRecordResponse(
        malpractice_count=new_count,
        is_terminal=is_terminal,
        event_index=event_index,
        current_round=round_number,
    )


async def put_session_terminated(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    reason: str,
) -> int:
    """Admin force-terminate a session.

    Sets status to TERMINATED, records completed_at, and pushes a WebSocket
    terminated event to the candidate.

    Returns:
        The current_round at time of termination (for background scoring).

    Raises:
        NotFoundException: If no submission with the given ID exists.
        ForbiddenException: If the submission is not in a terminable state.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)

    if sub.get("status") not in {SubmissionStatus.PENDING, SubmissionStatus.IN_PROGRESS}:
        raise ForbiddenException(
            f"Cannot terminate a submission with status '{sub.get('status')}'. "
            "Only pending or in-progress submissions can be terminated."
        )

    invalidate_answer_ctx(submission_id)
    now = utcnow()
    await db.assessment_submissions.update_one(
        {"_id": ObjectId(submission_id)},
        {
            "$set": {
                "status": SubmissionStatus.TERMINATED,
                "completed_at": now,
                "updated_at": now,
            }
        },
    )

    await manager.send_json(submission_id, {"type": "terminated", "reason": "admin"})
    logger.info(f"Session force-terminated: submission_id={submission_id} reason={reason}")
    return int(sub.get("current_round", 1))


async def put_session_completed(
    db: AsyncIOMotorDatabase,
    submission_id: str,
) -> int:
    """Admin force-complete a session.

    Sets status to COMPLETED and records completed_at.

    Returns:
        The current_round at time of completion (for background scoring).

    Raises:
        NotFoundException: If no submission with the given ID exists.
        ForbiddenException: If the submission is not in a completable state.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)

    if sub.get("status") not in {SubmissionStatus.PENDING, SubmissionStatus.IN_PROGRESS}:
        raise ForbiddenException(
            f"Cannot complete a submission with status '{sub.get('status')}'. "
            "Only pending or in-progress submissions can be completed."
        )

    invalidate_answer_ctx(submission_id)
    now = utcnow()
    await db.assessment_submissions.update_one(
        {"_id": ObjectId(submission_id)},
        {
            "$set": {
                "status": SubmissionStatus.COMPLETED,
                "completed_at": now,
                "updated_at": now,
            }
        },
    )

    logger.info(f"Session force-completed: submission_id={submission_id}")
    return int(sub.get("current_round", 1))


async def put_session_on_hold(
    db: AsyncIOMotorDatabase,
    submission_id: str,
) -> None:
    """Mark an IN_PROGRESS submission as ON_HOLD after network-loss timeout.

    Called by the WebSocket connection manager after HOLD_DELAY_SECONDS with no
    reconnection.  Safe to call if already ON_HOLD (idempotent).
    """
    now = utcnow()
    result = await db.assessment_submissions.update_one(
        {
            "_id": ObjectId(submission_id),
            "status": SubmissionStatus.IN_PROGRESS,
        },
        {
            "$set": {
                "status": SubmissionStatus.ON_HOLD,
                "paused_at": now,
                "updated_at": now,
            }
        },
    )
    if result.modified_count:
        logger.info("Session placed ON_HOLD: submission_id=%s", submission_id)


async def get_session_state(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str,
) -> SessionStateResponse:
    """Return the persisted position plus the server-computed remaining time.

    Raises:
        NotFoundException: If the submission is not found.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        raise NotFoundException("Submission not found")
    return SessionStateResponse(
        status=str(sub.get("status", "")),
        remaining_seconds=timing.remaining_seconds(sub),
        current_question_idx=sub.get("current_question_idx", 0),
        current_round=sub.get("current_round", 1),
    )


async def upload_malpractice_media(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    event_index: int,
    candidate_id: str,
    video_bytes: bytes | None = None,
    video_content_type: str = "video/webm",
    audio_bytes: bytes | None = None,
    audio_content_type: str = "audio/webm",
) -> None:
    """Upload video/audio clips for an existing malpractice event (Phase 2 of two-phase flow).

    Evidence is write-once: a clip already attached to an event can never be replaced, so
    a candidate cannot swap an incriminating recording for a benign one.

    Raises:
        NotFoundException: If submission not found or event_index out of range.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)},
        {
            "malpractice_events.round": 1,
            "malpractice_events.type": 1,
            "malpractice_events.timestamp": 1,
            "current_round": 1,
        },
    )
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)

    events = sub.get("malpractice_events") or []
    if event_index < 0 or event_index >= len(events):
        raise AppException(f"Invalid event_index: {event_index}")

    event = events[event_index]
    round_n = event.get("round", sub.get("current_round", 1))
    mal_type = event.get("type", "unknown")
    now = utcnow()
    ts = now.isoformat()
    # Identify the event by value, not by array index: a dotted `events.0.field` path also
    # matches an element field literally named "0", which would break the write-once check.
    identity: dict = (
        {"timestamp": event["timestamp"]}
        if event.get("timestamp") is not None
        else {"type": mal_type, "round": round_n}
    )

    async def _attach(field: str, data: bytes, evidence_type: str, content_type: str) -> None:
        key = s3_service.make_evidence_key(submission_id, round_n, mal_type, evidence_type, ts)
        # `field: None` matches a missing or null field — only an empty slot is writable.
        result = await db.assessment_submissions.update_one(
            {
                "_id": sub["_id"],
                "malpractice_events": {"$elemMatch": {**identity, field: None}},
            },
            {"$set": {f"malpractice_events.$.{field}": key, "updated_at": now}},
        )
        if result.modified_count:
            try:
                await s3_service.upload(data, key, content_type)
            except Exception:
                # Release the claimed slot so the client can retry the upload.
                await db.assessment_submissions.update_one(
                    {
                        "_id": sub["_id"],
                        "malpractice_events": {"$elemMatch": {**identity, field: key}},
                    },
                    {"$set": {f"malpractice_events.$.{field}": None}},
                )
                raise
            logger.info(
                f"Malpractice media uploaded: submission_id={submission_id} "
                f"event_index={event_index} field={field}"
            )

    tasks = []
    if video_bytes:
        tasks.append(_attach("screen_video_s3_key", video_bytes, "video_chunk", video_content_type))
    if audio_bytes:
        tasks.append(_attach("audio_clip_s3_key", audio_bytes, "audio_clip", audio_content_type))
    if tasks:
        await asyncio.gather(*tasks)


async def get_live_interviews(
    db: AsyncIOMotorDatabase,
    search: str | None,
    monitoring_type: str | None,
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
    workspace_ids: list[str] | None = None,
) -> dict:
    """Return paginated in-progress submissions joined with candidate and assessment data.

    ``workspace_ids`` restricts results to those workspaces (None = unrestricted, i.e. super
    admin; an empty list yields nothing).

    Supports filtering by monitoring_type and full-text search on candidate name/assessment name.
    """
    skip, limit = paginate_query(page, page_size)
    sort_dir = 1 if sort_order == "asc" else -1
    sort_field = sort_by if sort_by in ["started_at", "updated_at", "created_at"] else "started_at"

    pipeline: list[Any] = [
        {_MATCH: {"status": "in_progress"}},
        {
            "$lookup": {
                "from": "assessments",
                "localField": "assessment_id",
                "foreignField": "_id",
                "as": "assessment",
            }
        },
        {"$unwind": "$assessment"},
        *(
            [
                {
                    _MATCH: {
                        "assessment.workspace_id": {
                            "$in": [ObjectId(w) for w in workspace_ids if ObjectId.is_valid(w)]
                        }
                    }
                }
            ]
            if workspace_ids is not None
            else []
        ),
        {
            "$lookup": {
                "from": "users",
                "localField": "candidate_id",
                "foreignField": "_id",
                "as": "candidate",
            }
        },
        {"$unwind": "$candidate"},
        {
            "$addFields": {
                "submission_id": {"$toString": "$_id"},
                "workspace_id": {"$toString": "$assessment.workspace_id"},
                "candidate_name": {
                    "$trim": {
                        "input": {
                            "$concat": [
                                {"$ifNull": ["$candidate.first_name", ""]},
                                " ",
                                {"$ifNull": ["$candidate.last_name", ""]},
                            ]
                        }
                    }
                },
                "assessment_name": "$assessment.name",
            }
        },
    ]

    if monitoring_type:
        pipeline.append({_MATCH: {"assessment.accessibility": monitoring_type}})
    if search:
        escaped = safe_regex(search)
        pipeline.append(
            {
                _MATCH: {
                    "$or": [
                        {"candidate.first_name": {_REGEX: escaped, _OPTIONS: "i"}},
                        {"candidate.last_name": {_REGEX: escaped, _OPTIONS: "i"}},
                        {"candidate.email": {_REGEX: escaped, _OPTIONS: "i"}},
                        {"assessment.name": {_REGEX: escaped, _OPTIONS: "i"}},
                    ]
                }
            }
        )

    pipeline.append(
        {
            "$project": {
                "rounds_data": 0,
                "candidate": 0,
                "assessment": 0,
            }
        }
    )

    count_res = await db.assessment_submissions.aggregate(pipeline + [{"$count": "total"}]).to_list(
        1
    )
    total = count_res[0]["total"] if count_res else 0

    pipeline += [{"$sort": {sort_field: sort_dir}}, {"$skip": skip}, {"$limit": limit}]
    docs = await db.assessment_submissions.aggregate(pipeline).to_list(limit)

    return {
        "live_interviews": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }
