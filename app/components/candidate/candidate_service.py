import random
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import QuestionType, SubmissionStatus
from app.common.exceptions import AppException, ForbiddenException, NotFoundException
from app.common.utils import (
    build_pagination_meta,
    decode_sharelink,
    paginate_query,
    safe_regex,
    serialize_doc,
    serialize_docs,
    utcnow,
)
from app.components.storage import s3_service
from app.components.websocket.connection_manager import admin_manager, manager
from app.core.config import settings
from app.core.logging import logger

_DEFAULT_CONTENT_TYPE = "image/jpeg"

_ERR_ASSESSMENT_NOT_FOUND = "Assessment not found"
_ERR_ACTIVE_SUBMISSION_NOT_FOUND = "Active submission not found"

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


async def get_candidate_assessment(db: AsyncIOMotorDatabase, share_link: str) -> dict:
    """Return assessment metadata for a candidate via share link, without internal question IDs.

    Also patches monitoring_overrides from the matching assessment_shares document so the
    frontend receives the effective monitoring config for this specific share link.

    Raises:
        NotFoundException: If the share link is invalid.
    """
    from app.common.exceptions import ValidationException

    # 1. Look up share document first — carries monitoring_overrides and assessment_id.
    share_doc = await db.assessment_shares.find_one({"share_link": share_link, "is_active": True})

    doc = None
    if share_doc:
        doc = await db.assessments.find_one({"_id": share_doc["assessment_id"], "is_active": True})
    else:
        # 2. Fall back: decode a signed link directly.
        try:
            decoded = decode_sharelink(share_link)
            doc = await db.assessments.find_one({"_id": ObjectId(decoded["a"]), "is_active": True})
        except (ValidationException, Exception):
            # 3. Legacy: plain share_link field on the assessment document.
            doc = await db.assessments.find_one({"share_link": share_link, "is_active": True})

    if not doc:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    safe = serialize_doc(doc)
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
        return serialize_doc(existing)
    await db.assessment_submissions.update_one(
        {"_id": existing["_id"]},
        {
            "$set": {
                "status": SubmissionStatus.IN_PROGRESS,
                "started_at": utcnow(),
                "updated_at": utcnow(),
            }
        },
    )
    updated = await db.assessment_submissions.find_one({"_id": existing["_id"]})
    return serialize_doc(updated)


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
        "options": options,
    }


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
    safe_questions = [_sanitize_question(q) for q in questions_raw]
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


async def start_assessment(db: AsyncIOMotorDatabase, share_link: str, candidate_id: str) -> dict:
    """Start or resume a candidate's assessment submission.

    - Returns existing submission if already IN_PROGRESS or ON_HOLD.
    - Transitions PENDING → IN_PROGRESS without re-sampling questions.
    - Creates a new submission on first call, pulling monitoring_overrides from the
      assessment_shares document that matches the share link.

    Raises:
        NotFoundException: If the assessment share link is invalid.
        ForbiddenException: If the submission is already COMPLETED or MALPRACTICE.
    """
    assessment = None
    share_doc: dict | None = None

    # 1. Look up the share document — carries monitoring_overrides and assessment_id.
    share_doc = await db.assessment_shares.find_one({"share_link": share_link, "is_active": True})
    if share_doc:
        assessment = await db.assessments.find_one(
            {"_id": share_doc["assessment_id"], "is_active": True}
        )
    else:
        # 2. Fall back: decode a signed link (expirable / permanent) directly.
        try:
            decoded = decode_sharelink(share_link)
            assessment = await db.assessments.find_one(
                {"_id": ObjectId(decoded["a"]), "is_active": True}
            )
        except Exception:
            # 3. Legacy: plain share_link field on the assessment document.
            assessment = await db.assessments.find_one(
                {"share_link": share_link, "is_active": True}
            )

    if not assessment:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    existing = await db.assessment_submissions.find_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
        }
    )
    if existing:
        return await _handle_existing_submission(db, existing)

    rounds_data = [
        await _build_round_data(db, round_cfg) for round_cfg in assessment.get("rounds", [])
    ]

    monitoring_overrides: dict | None = share_doc.get("monitoring_overrides") if share_doc else None

    now = utcnow()
    for rd in rounds_data:
        if rd.get("round_number") == 1:
            rd["started_at"] = now
            break

    submission = {
        "assessment_id": assessment["_id"],
        "candidate_id": ObjectId(candidate_id),
        "share_id": ObjectId(share_doc["_id"]) if share_doc else None,
        "monitoring_overrides": monitoring_overrides,
        "status": SubmissionStatus.IN_PROGRESS,
        "rounds_data": rounds_data,
        "current_round": 1,
        "score": 0,
        "percentage": 0.0,
        "screenshots": [],
        "malpractice_count": 0,
        "malpractice_events": [],
        "reaccess_count": 0,
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
    return serialize_doc(submission)


async def get_submission_status(
    db: AsyncIOMotorDatabase, share_link: str, candidate_id: str
) -> dict | None:
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
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    sub = await db.assessment_submissions.find_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
        }
    )
    if not sub:
        return None

    return {
        "submission_id": str(sub["_id"]),
        "status": sub.get("status"),
        "assessment_id": str(sub["assessment_id"]),
        "candidate_id": str(sub["candidate_id"]),
        "current_round": sub.get("current_round", 1),
        "completed_at": sub["completed_at"].isoformat() if sub.get("completed_at") else None,
        "paused_at": sub["paused_at"].isoformat() if sub.get("paused_at") else None,
        "malpractice_count": sub.get("malpractice_count", 0),
    }


async def get_current_round(
    db: AsyncIOMotorDatabase, submission_id: str, candidate_id: str
) -> dict:
    """Return the questions and metadata for the candidate's current round.

    Correct answers and is_correct flags are never included in the response.

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
    if sub_status == SubmissionStatus.ON_HOLD:
        # Allow reading round data so the frontend can restore UI; it will show the
        # ON_HOLD overlay via WebSocket rather than blocking entirely.
        pass

    assessment = await db.assessments.find_one({"_id": sub["assessment_id"], "is_active": True})
    current = sub.get("current_round", 1)
    idx = current - 1
    rounds_data = sub.get("rounds_data", [])

    if idx >= len(rounds_data):
        raise NotFoundException("Round not found")

    rd = rounds_data[idx]

    # Start from assessment-level monitoring defaults
    base_monitoring: dict = {}
    if assessment:
        base_monitoring = assessment.get("monitoring_config") or {}

    # Apply per-candidate overrides stored on the submission (from schedule)
    candidate_overrides: dict = sub.get("monitoring_overrides") or {}
    effective: dict = {
        **base_monitoring,
        **{k: v for k, v in candidate_overrides.items() if v is not None},
    }

    return {
        "round": {
            "round_number": current,
            "questions": rd.get("questions", []),
            "max_duration_minutes": rd.get("max_duration_minutes", 30),
        },
        "tab_monitoring": effective.get("tab_monitoring", False),
        "audio_monitoring": effective.get("audio_monitoring", False),
        "video_monitoring": effective.get("video_monitoring", False),
        "screenshot_enabled": effective.get("screenshot_enabled", False),
        "screenshot_mode": effective.get("screenshot_mode", "time_interval"),
        "screenshot_interval_seconds": effective.get("screenshot_interval_seconds"),
        "screenshot_count": effective.get("screenshot_count"),
        # Persisted session state for seamless resume after network loss
        "remaining_seconds": sub.get("remaining_seconds"),
        "current_question_idx": sub.get("current_question_idx", 0),
        "session_status": sub_status.value if sub_status else "in_progress",
    }


async def submit_answer(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str,
    question_id: str,
    answer: Any,
) -> dict:
    """Persist a candidate's answer for a question in the current round.

    Raises:
        NotFoundException: If no active in-progress submission exists.
    """
    sub = await db.assessment_submissions.find_one(
        {
            "_id": ObjectId(submission_id),
            "candidate_id": ObjectId(candidate_id),
            "status": {"$in": [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]},
        }
    )
    if not sub:
        raise NotFoundException(_ERR_ACTIVE_SUBMISSION_NOT_FOUND)

    idx = sub.get("current_round", 1) - 1
    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {
            "$set": {
                f"rounds_data.{idx}.answers.{question_id}": answer,
                "updated_at": utcnow(),
            }
        },
    )
    return {"saved": True}


async def finish_round(db: AsyncIOMotorDatabase, submission_id: str, candidate_id: str) -> dict:
    """Mark the current round complete and advance or finalise the assessment.

    Does NOT score the round — scoring is handled by a background task after this returns.

    Returns:
        {'completed': True, 'finished_round': int} if all rounds are done,
        {'completed': False, 'next_round': int, 'finished_round': int} otherwise.

    Raises:
        NotFoundException: If no active submission or round is found.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        raise NotFoundException(_ERR_ACTIVE_SUBMISSION_NOT_FOUND)

    assessment = await db.assessments.find_one({"_id": sub["assessment_id"], "is_active": True})
    if not assessment:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    current = sub.get("current_round", 1)
    total_rounds = len(assessment.get("rounds", []))

    current_rd = next(
        (rd for rd in sub.get("rounds_data", []) if rd.get("round_number") == current),
        None,
    )
    if not current_rd:
        raise NotFoundException("Round not found")

    now = utcnow()
    is_last_round = current >= total_rounds

    set_fields: dict = {
        "rounds_data.$[rd].completed": True,
        "rounds_data.$[rd].completed_at": now,
        "updated_at": now,
    }

    if is_last_round:
        set_fields["status"] = SubmissionStatus.COMPLETED
        set_fields["completed_at"] = now
    else:
        set_fields["current_round"] = current + 1
        set_fields["remaining_seconds"] = None
        set_fields["current_question_idx"] = 0

    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {"$set": set_fields},
        array_filters=[{"rd.round_number": current}],
    )

    if not is_last_round:
        await db.assessment_submissions.update_one(
            {"_id": sub["_id"]},
            {"$set": {"rounds_data.$[nxt].started_at": now}},
            array_filters=[{"nxt.round_number": current + 1}],
        )

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

    Silently no-ops if the submission is not found.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
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
    """Upload applicable evidence files to S3 and return a dict of S3 keys.

    Keys in the returned dict: screen_image_s3_key, face_image_s3_key,
    screen_video_s3_key, audio_clip_s3_key.  Each is None when not uploaded.
    """
    keys: dict = {
        "screen_image_s3_key": None,
        "face_image_s3_key": None,
        "screen_video_s3_key": None,
        "audio_clip_s3_key": None,
    }

    def _extract(data: tuple | bytes | None, fallback_ct: str) -> tuple[bytes, str] | None:
        """Unpack a (bytes, content_type) tuple or a bare bytes value."""
        if data is None:
            return None
        if isinstance(data, tuple):
            return data[0], data[1] or fallback_ct
        return data, fallback_ct

    if effective_monitoring.get("screenshot_enabled", False):
        screen_data = _extract(file_bytes_map.get("screen_image"), _DEFAULT_CONTENT_TYPE)
        if screen_data:
            screen_bytes, screen_ct = screen_data
            key = s3_service.make_evidence_key(
                submission_id, round_number, malpractice_type, "screen_image", now.isoformat()
            )
            await s3_service.upload(screen_bytes, key, screen_ct)
            keys["screen_image_s3_key"] = key

    if effective_monitoring.get("video_monitoring", False):
        face_data = _extract(file_bytes_map.get("face_image"), _DEFAULT_CONTENT_TYPE)
        if face_data:
            face_bytes, face_ct = face_data
            key = s3_service.make_evidence_key(
                submission_id, round_number, malpractice_type, "face_image", now.isoformat()
            )
            await s3_service.upload(face_bytes, key, face_ct)
            keys["face_image_s3_key"] = key

        video_data = _extract(file_bytes_map.get("video_chunk"), "video/webm")
        if video_data:
            video_bytes, video_ct = video_data
            key = s3_service.make_evidence_key(
                submission_id, round_number, malpractice_type, "video_chunk", now.isoformat()
            )
            await s3_service.upload(video_bytes, key, video_ct)
            keys["screen_video_s3_key"] = key

    if effective_monitoring.get("audio_monitoring", False):
        audio_data = _extract(file_bytes_map.get("audio_clip"), "audio/webm")
        if audio_data:
            audio_bytes, audio_ct = audio_data
            key = s3_service.make_evidence_key(
                submission_id, round_number, malpractice_type, "audio_clip", now.isoformat()
            )
            await s3_service.upload(audio_bytes, key, audio_ct)
            keys["audio_clip_s3_key"] = key

    return keys


async def _persist_malpractice_event(
    db: AsyncIOMotorDatabase,
    sub: dict,
    submission_id: str,
    event_data: dict,
    new_count: int,
    is_terminal: bool,
    now: Any,
    workspace_id: str,
) -> None:
    """Write the malpractice event to DB, notify candidate if terminal, and broadcast to admins."""
    update_set: dict = {"malpractice_count": new_count, "updated_at": now}
    if is_terminal:
        update_set["status"] = SubmissionStatus.MALPRACTICE
        update_set["completed_at"] = now

    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {"$set": update_set, "$push": {"malpractice_events": event_data}},
    )

    if is_terminal:
        await manager.send_json(submission_id, {"type": "terminated", "reason": "malpractice"})

    # Broadcast real-time malpractice event to all admins monitoring this workspace
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
) -> dict:
    """Record a malpractice event using the 3-strike system.

    Accepts an optional file_bytes_map with keys: screen_image, face_image,
    video_chunk, audio_clip.  Files are conditionally uploaded to S3 based on
    the effective monitoring configuration.

    On the 3rd strike (malpractice_count >= MAX_MALPRACTICE_COUNT) the submission
    is terminated and a WebSocket event is pushed to the candidate.

    Raises:
        NotFoundException: If no matching submission exists.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        raise NotFoundException(_ERR_ACTIVE_SUBMISSION_NOT_FOUND)

    # ── Resolve effective monitoring config ────────────────────────────────
    assessment = await db.assessments.find_one({"_id": sub["assessment_id"], "is_active": True})
    base_monitoring: dict = (assessment.get("monitoring_config") or {}) if assessment else {}
    candidate_overrides: dict = sub.get("monitoring_overrides") or {}
    effective_monitoring: dict = {
        **base_monitoring,
        **{k: v for k, v in candidate_overrides.items() if v is not None},
    }
    workspace_id = str(assessment["workspace_id"]) if assessment else ""

    # ── Guard: silently skip if monitoring is disabled for this event type ──
    if not _is_malpractice_event_enabled(malpractice_type, effective_monitoring):
        return {"malpractice_count": sub.get("malpractice_count", 0), "is_terminal": False}

    now = utcnow()
    round_number = sub.get("current_round", 1)

    # ── Upload evidence files to S3 ────────────────────────────────────────
    s3_keys = await _upload_evidence_files(
        submission_id,
        round_number,
        malpractice_type,
        now,
        effective_monitoring,
        file_bytes_map or {},
    )

    # ── Build event record ─────────────────────────────────────────────────
    new_count = (sub.get("malpractice_count") or 0) + 1
    is_terminal = new_count >= settings.MAX_MALPRACTICE_COUNT
    # event_index is the 0-based position this event will occupy after $push
    event_index = len(sub.get("malpractice_events") or [])

    event_data: dict = {
        "type": malpractice_type,
        "description": description or "",
        "timestamp": now,
        "round": round_number,
        "is_terminal": is_terminal,
        **s3_keys,
    }

    # ── Persist and (if terminal) notify via WebSocket ─────────────────────
    await _persist_malpractice_event(
        db, sub, submission_id, event_data, new_count, is_terminal, now, workspace_id
    )

    if is_terminal:
        logger.warning(
            f"Malpractice terminal: submission_id={submission_id} type={malpractice_type} "
            f"count={new_count}"
        )
    else:
        logger.info(
            f"Malpractice flagged: submission_id={submission_id} type={malpractice_type} "
            f"count={new_count}"
        )

    return {
        "malpractice_count": new_count,
        "is_terminal": is_terminal,
        "event_index": event_index,
        "current_round": round_number,
    }


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
        raise NotFoundException(_ERR_ACTIVE_SUBMISSION_NOT_FOUND)

    if sub.get("status") not in {SubmissionStatus.PENDING, SubmissionStatus.IN_PROGRESS}:
        raise ForbiddenException(
            f"Cannot terminate a submission with status '{sub.get('status')}'. "
            "Only pending or in-progress submissions can be terminated."
        )

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
        raise NotFoundException(_ERR_ACTIVE_SUBMISSION_NOT_FOUND)

    if sub.get("status") not in {SubmissionStatus.PENDING, SubmissionStatus.IN_PROGRESS}:
        raise ForbiddenException(
            f"Cannot complete a submission with status '{sub.get('status')}'. "
            "Only pending or in-progress submissions can be completed."
        )

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
) -> dict:
    """Return the persisted timer/position state for a candidate's submission.

    Used by the frontend on reconnect to restore the timer and question position
    without a full round reload.

    Raises:
        NotFoundException: If the submission is not found.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)},
        {
            "status": 1,
            "remaining_seconds": 1,
            "current_question_idx": 1,
            "current_round": 1,
        },
    )
    if not sub:
        raise NotFoundException("Submission not found")
    return {
        "status": str(sub.get("status", "")),
        "remaining_seconds": sub.get("remaining_seconds"),
        "current_question_idx": sub.get("current_question_idx", 0),
        "current_round": sub.get("current_round", 1),
    }


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

    Validates ownership and index bounds, uploads to S3, then patches the specific
    malpractice_events array element using the positional dot-notation update.

    Raises:
        NotFoundException: If submission not found or event_index out of range.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        raise NotFoundException(_ERR_ACTIVE_SUBMISSION_NOT_FOUND)

    events = sub.get("malpractice_events") or []
    if event_index < 0 or event_index >= len(events):
        raise AppException(f"Invalid event_index: {event_index}")

    event = events[event_index]
    round_n = event.get("round", sub.get("current_round", 1))
    mal_type = event.get("type", "unknown")
    now = utcnow()
    ts = now.isoformat()

    update_fields: dict = {}

    if video_bytes:
        key = s3_service.make_evidence_key(submission_id, round_n, mal_type, "video_chunk", ts)
        await s3_service.upload(video_bytes, key, video_content_type)
        update_fields[f"malpractice_events.{event_index}.screen_video_s3_key"] = key

    if audio_bytes:
        key = s3_service.make_evidence_key(submission_id, round_n, mal_type, "audio_clip", ts)
        await s3_service.upload(audio_bytes, key, audio_content_type)
        update_fields[f"malpractice_events.{event_index}.audio_clip_s3_key"] = key

    if update_fields:
        update_fields["updated_at"] = now
        await db.assessment_submissions.update_one(
            {"_id": sub["_id"]},
            {"$set": update_fields},
        )
        logger.info(
            f"Malpractice media uploaded: submission_id={submission_id} "
            f"event_index={event_index} fields={list(update_fields.keys())}"
        )


async def get_live_interviews(
    db: AsyncIOMotorDatabase,
    search: str | None,
    monitoring_type: str | None,
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
) -> dict:
    """Return paginated in-progress submissions joined with candidate and assessment data.

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
