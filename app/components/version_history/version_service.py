from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import (
    QuestionType,
    SubmissionStatus,
)
from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.utils import serialize_doc, utcnow
from app.components.storage import s3_service
from app.core.config import settings
from app.core.logging import logger

_ERR_SUBMISSION_NOT_FOUND = "Submission not found"

# MCQ question types that admit a correct/incorrect judgement
_MCQ_TYPES = frozenset(
    [QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI, "mcq_single", "mcq_multiple", "mcq_multi"]
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _resolve_s3_url(key: str | None) -> str | None:
    """Return a presigned URL for an S3 key, or None if the key is falsy or on error."""
    if not key:
        return None
    try:
        return await s3_service.get_presigned_url(key)
    except Exception:
        return None


def _iso(dt: Any) -> str | None:
    """Convert a datetime (or ISO string) to an ISO string, or return None."""
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return str(dt.isoformat())
    return str(dt)


# ---------------------------------------------------------------------------
# Public read helpers
# ---------------------------------------------------------------------------


async def get_versions_list(db: AsyncIOMotorDatabase, submission_id: str) -> list[dict]:
    """Return a list of version history entries for a submission, sorted by version ASC.

    Each entry contains summary fields only (no full round/question payload).

    Args:
        db: AsyncIOMotorDatabase instance.
        submission_id: String representation of the submission ObjectId.

    Returns:
        List of dicts with keys: version, status, score, percentage, malpractice_count,
        reaccess_reason, reaccess_reason_category, started_at, completed_at.
    """
    cursor = db.assessment_version_history.find({"submission_id": ObjectId(submission_id)}).sort(
        "version", 1
    )
    docs = await cursor.to_list(None)

    result = []
    for doc in docs:
        result.append(
            {
                "version": doc.get("version"),
                "status": doc.get("status"),
                "score": doc.get("score", 0),
                "percentage": doc.get("percentage", 0.0),
                "malpractice_count": doc.get("malpractice_count", 0),
                "reaccess_reason": doc.get("reaccess_reason"),
                "reaccess_reason_category": doc.get("reaccess_reason_category"),
                "started_at": _iso(doc.get("started_at")),
                "completed_at": _iso(doc.get("completed_at")),
            }
        )
    return result


async def get_version_detail(
    db: AsyncIOMotorDatabase, submission_id: str, version: int
) -> dict | None:
    """Return a single version history document for a submission + version number.

    Args:
        db: AsyncIOMotorDatabase instance.
        submission_id: String representation of the submission ObjectId.
        version: Version number to retrieve.

    Returns:
        Serialized document dict, or None if not found.
    """
    doc = await db.assessment_version_history.find_one(
        {
            "submission_id": ObjectId(submission_id),
            "version": version,
        }
    )
    if doc is None:
        return None
    return serialize_doc(doc)


# ---------------------------------------------------------------------------
# Low-level question-answer helpers (shared by snapshot and live builders)
# ---------------------------------------------------------------------------


def _extract_q_id(q: dict) -> str:
    r"""Return the string question ID from a question dict (handles id/\_id/question_id keys)."""
    return str(q.get("id") or q.get("_id") or q.get("question_id") or "")


def _collect_question_ids(rounds_data: list) -> list[ObjectId]:
    """Collect all parseable ObjectIds across all round question lists."""
    ids: list[ObjectId] = []
    for rd in rounds_data:
        for q in rd.get("questions", []):
            raw = q.get("id") or q.get("_id") or q.get("question_id")
            if raw:
                try:
                    ids.append(ObjectId(str(raw)))
                except Exception:
                    logger.debug("Skipping non-ObjectId question id: %r", raw)
    return ids


async def _fetch_originals(db: AsyncIOMotorDatabase, ids: list[ObjectId]) -> dict[str, dict]:
    """Batch-fetch question originals from the question bank; return a str-id keyed map."""
    if not ids:
        return {}
    docs = await db.questions.find({"_id": {"$in": ids}}).to_list(len(ids))
    return {str(d["_id"]): d for d in docs}


def _compute_is_correct(
    question_type: str | None,
    raw_options: list,
    candidate_answer: Any,
) -> bool | None:
    """Return True/False for MCQ questions, None for essay/unknown types."""
    if question_type not in _MCQ_TYPES:
        return None
    correct_ids = {opt["id"] for opt in raw_options if opt.get("is_correct")}
    given = [candidate_answer] if isinstance(candidate_answer, str) else (candidate_answer or [])
    return set(given) == correct_ids


def _build_question_answer(q: dict, original: dict, candidate_answer: Any) -> dict:
    """Build a single question_answer dict from the live question and its bank original."""
    raw_options = original.get("options", [])
    options = [
        {"id": opt.get("id"), "text": opt.get("text"), "is_correct": opt.get("is_correct", False)}
        for opt in raw_options
    ]
    question_type = original.get("question_type") or q.get("type") or q.get("question_type")
    return {
        "question_id": _extract_q_id(q),
        "question_text": original.get("question_text", q.get("text", "")),
        "question_type": question_type,
        "options": options,
        "candidate_answer": candidate_answer,
        "is_correct": _compute_is_correct(question_type, raw_options, candidate_answer),
    }


def _build_question_answer_from_embedded(
    q: dict, original: dict, candidate_answer: Any, q_result: dict
) -> dict:
    """Build a question_answer dict using embedded rounds_data for question text/type/options
    and the DB original for per-option is_correct. Question-level is_correct comes from
    question_results rather than being re-computed from options.
    """
    correctness: dict = {
        opt.get("id"): opt.get("is_correct", False) for opt in original.get("options", ())
    }
    options = [
        {
            "id": (oid := opt.get("id")),
            "text": opt.get("text"),
            "is_correct": correctness.get(oid, False),
        }
        for opt in q.get("options", ())
    ]
    return {
        "question_id": _extract_q_id(q),
        "question_text": q.get("text", ""),
        "question_type": q.get("type") or q.get("question_type"),
        "options": options,
        "candidate_answer": candidate_answer,
        "is_correct": q_result.get("is_correct") if q_result else None,
    }


def _build_round_entry(rd: dict, question_answers: list) -> dict:
    """Assemble a single round dict from a rounds_data entry and its resolved question_answers."""
    question_results = rd.get("question_results", {})
    return {
        "round_number": rd.get("round_number"),
        "score": rd.get("score", 0),
        "percentage": rd.get("percentage", 0.0),
        "started_at": _iso(rd.get("started_at")),
        "completed_at": _iso(rd.get("completed_at")),
        "question_answers": question_answers,
        "is_validated": len(question_results) > 1,
    }


def _build_rounds_from_originals(rounds_data: list, originals: dict[str, dict]) -> list:
    """Assemble all round entries given a pre-fetched originals map."""
    result = []
    for rd in rounds_data:
        answers: dict = rd.get("answers", {})
        question_answers = [
            _build_question_answer(
                q, originals.get(_extract_q_id(q), {}), answers.get(_extract_q_id(q), [])
            )
            for q in rd.get("questions", [])
        ]
        result.append(_build_round_entry(rd, question_answers))
    return result


# ---------------------------------------------------------------------------
# Round snapshot builder (shared by archive and get_candidate_submission)
# ---------------------------------------------------------------------------


async def build_round_snapshot(rounds_data: list, db: AsyncIOMotorDatabase) -> list:
    """Convert the rounds_data array from assessment_submissions into a flat snapshot format
    suitable for storing in assessment_version_history.

    Batches all question lookups into a single DB query per call.

    Args:
        rounds_data: The rounds_data list from an assessment_submissions document.
        db: AsyncIOMotorDatabase instance (used to fetch original question documents).

    Returns:
        List of round dicts, each containing:
            - round_number (int)
            - score (int)
            - percentage (float)
            - started_at (str | None, ISO format)
            - completed_at (str | None, ISO format)
            - question_answers (list of question answer dicts)
    """
    originals = await _fetch_originals(db, _collect_question_ids(rounds_data))
    return _build_rounds_from_originals(rounds_data, originals)


# ---------------------------------------------------------------------------
# Live rounds builder (version=current, no DB snapshot)
# ---------------------------------------------------------------------------


async def _build_live_rounds(rounds_data: list, db: AsyncIOMotorDatabase) -> list:
    """Build question_answers for a live submission.

    Batches all question lookups into a single DB query for option is_correct.
    Uses embedded question text/type from rounds_data and question_results for
    the question-level is_correct instead of re-computing it from options.
    """
    originals = await _fetch_originals(db, _collect_question_ids(rounds_data))
    result = []
    for rd in rounds_data:
        answers: dict = rd.get("answers", {})
        question_results: dict = rd.get("question_results", {})
        question_answers = [
            _build_question_answer_from_embedded(
                q,
                originals.get(_extract_q_id(q), {}),
                answers.get(_extract_q_id(q), []),
                question_results.get(_extract_q_id(q), {}),
            )
            for q in rd.get("questions", [])
        ]
        result.append(_build_round_entry(rd, question_answers))
    return result


# ---------------------------------------------------------------------------
# CandidateDetailsPage main endpoint
# ---------------------------------------------------------------------------


async def get_candidate_submission(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    candidate_id: str,
    version: str = "current",
) -> dict:
    """Return the full CandidateDetailsPage payload for a given submission + version.

    Args:
        db: AsyncIOMotorDatabase instance.
        assessment_id: String ObjectId of the assessment.
        candidate_id: String ObjectId of the candidate.
        version: "current" for the live submission, or an integer string (e.g. "1", "2")
                 for a historical version from assessment_version_history.

    Returns:
        CandidateSubmissionResponse-shaped dict.

    Raises:
        NotFoundException: If the submission or requested version is not found.
    """
    # Fetch the live submission first — needed for all paths
    sub = await db.assessment_submissions.find_one(
        {
            "assessment_id": ObjectId(assessment_id),
            "candidate_id": ObjectId(candidate_id),
        }
    )
    if not sub:
        raise NotFoundException(_ERR_SUBMISSION_NOT_FOUND)

    submission_id_str = str(sub["_id"])

    # Fetch available versions list (summary only) for all paths
    available_versions_cursor = db.assessment_version_history.find(
        {"submission_id": sub["_id"]}
    ).sort("version", 1)
    version_docs = await available_versions_cursor.to_list(None)
    available_versions = [
        {
            "version": vd.get("version"),
            "status": vd.get("status"),
            "percentage": vd.get("percentage", 0.0),
            "started_at": _iso(vd.get("started_at")),
            "completed_at": _iso(vd.get("completed_at")),
        }
        for vd in version_docs
    ]

    # current_version is the max version number (= reaccess_count on live submission)
    current_version: int = sub.get("reaccess_count", 0)

    # ---------- HISTORICAL VERSION ----------
    if version != "current":
        try:
            version_int = int(version)
        except ValueError:
            raise NotFoundException(f"Invalid version: {version!r}") from None

        hist = await db.assessment_version_history.find_one(
            {"submission_id": sub["_id"], "version": version_int}
        )
        if not hist:
            raise NotFoundException(f"Version {version_int} not found for this submission")

        # Fetch candidate (exclude password_hash)
        candidate_doc = await db.users.find_one(
            {"_id": sub["candidate_id"]},
            {"password_hash": 0},
        )
        candidate_data = _build_candidate_dict(candidate_doc)

        # Resolve S3 URLs in malpractice events
        malpractice_events = await _resolve_malpractice_events(hist.get("malpractice_events", []))

        # Resolve S3 URLs in screenshots
        screenshots = await _resolve_screenshots(hist.get("screenshots", []))

        return {
            "candidate": candidate_data,
            "submission_id": submission_id_str,
            "status": hist.get("status"),
            "score": hist.get("score", 0),
            "percentage": hist.get("percentage", 0.0),
            "malpractice_count": hist.get("malpractice_count", 0),
            "reaccess_count": hist.get("reaccess_count", 0),
            "started_at": _iso(hist.get("started_at")),
            "completed_at": _iso(hist.get("completed_at")),
            "current_version": current_version,
            "available_versions": available_versions,
            "rounds": [
                {**r, "is_validated": r.get("is_validated", False)} for r in hist.get("rounds", [])
            ],
            "malpractice_events": malpractice_events,
            "screenshots": screenshots,
        }

    # ---------- CURRENT (LIVE) VERSION ----------
    # Fetch candidate (exclude password_hash)
    candidate_doc = await db.users.find_one(
        {"_id": sub["candidate_id"]},
        {"password_hash": 0},
    )
    candidate_data = _build_candidate_dict(candidate_doc)

    rounds = await _build_live_rounds(sub.get("rounds_data", []), db)

    # Resolve S3 URLs in malpractice events
    malpractice_events = await _resolve_malpractice_events(sub.get("malpractice_events", []))

    # Resolve S3 URLs in screenshots
    screenshots = await _resolve_screenshots(sub.get("screenshots", []))

    return {
        "candidate": candidate_data,
        "submission_id": submission_id_str,
        "status": sub.get("status"),
        "score": sub.get("score", 0),
        "percentage": sub.get("percentage", 0.0),
        "malpractice_count": sub.get("malpractice_count", 0),
        "reaccess_count": sub.get("reaccess_count", 0),
        "started_at": _iso(sub.get("started_at")),
        "completed_at": _iso(sub.get("completed_at")),
        "current_version": current_version,
        "available_versions": available_versions,
        "rounds": rounds,
        "malpractice_events": malpractice_events,
        "screenshots": screenshots,
    }


# ---------------------------------------------------------------------------
# Private shape helpers
# ---------------------------------------------------------------------------


def _build_candidate_dict(doc: dict | None) -> dict:
    """Extract candidate fields from a users document into the response shape."""
    if not doc:
        return {}
    return {
        "id": str(doc.get("_id", "")),
        "first_name": doc.get("first_name"),
        "last_name": doc.get("last_name"),
        "email": doc.get("email"),
        "phone": doc.get("phone"),
        "gender": doc.get("gender"),
        "dob": doc.get("dob"),
        "institution": doc.get("institution"),
        "location": doc.get("location"),
    }


async def _resolve_malpractice_events(events: list) -> list:
    """Convert S3 keys in malpractice events to presigned URLs."""
    resolved = []
    for ev in events:
        resolved.append(
            {
                "type": ev.get("type"),
                "round": ev.get("round"),
                "timestamp": _iso(ev.get("timestamp")),
                "screen_image_url": await _resolve_s3_url(ev.get("screen_image_s3_key")),
                "face_image_url": await _resolve_s3_url(ev.get("face_image_s3_key")),
                "screen_video_url": await _resolve_s3_url(ev.get("screen_video_s3_key")),
                "audio_clip_url": await _resolve_s3_url(ev.get("audio_clip_s3_key")),
                "is_terminal": ev.get("is_terminal", False),
            }
        )
    return resolved


async def _resolve_screenshots(screenshots: list) -> list:
    """Convert S3 keys in screenshots to presigned URLs."""
    resolved = []
    for sc in screenshots:
        resolved.append(
            {
                "url": await _resolve_s3_url(sc.get("s3_key")),
                "round": sc.get("round"),
                "taken_at": _iso(sc.get("taken_at")),
            }
        )
    return resolved


# ---------------------------------------------------------------------------
# Archive submission
# ---------------------------------------------------------------------------


async def archive_submission(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    admin_id: str,
    reason: str,
    reason_category: str,
) -> int:
    """Archive the current submission state to assessment_version_history.

    Called before granting re-access so that the candidate's previous attempt
    is preserved as a versioned snapshot.

    Args:
        db: AsyncIOMotorDatabase instance.
        submission_id: String ObjectId of the submission to archive.
        admin_id: String ObjectId of the admin performing the action.
        reason: Human-readable reason for the re-access grant.
        reason_category: One of ReaccessReasonCategory values.

    Returns:
        The version number that was archived (1-based, = reaccess_count + 1).

    Raises:
        NotFoundException: If the submission does not exist.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException(_ERR_SUBMISSION_NOT_FOUND)

    version: int = int(sub.get("reaccess_count", 0)) + 1

    # Build round snapshot with embedded question text
    round_snapshot = await build_round_snapshot(sub.get("rounds_data", []), db)

    now = utcnow()
    history_doc = {
        "submission_id": sub["_id"],
        "assessment_id": sub.get("assessment_id"),
        "candidate_id": sub.get("candidate_id"),
        "version": version,
        "status": sub.get("status"),
        "score": sub.get("score", 0),
        "percentage": sub.get("percentage", 0.0),
        "malpractice_count": sub.get("malpractice_count", 0),
        "malpractice_events": sub.get("malpractice_events", []),
        "screenshots": sub.get("screenshots", []),
        "rounds": round_snapshot,
        "reaccess_reason": reason,
        "reaccess_reason_category": reason_category,
        "archived_by": ObjectId(admin_id),
        "started_at": sub.get("started_at"),
        "completed_at": sub.get("completed_at"),
        "archived_at": now,
        # Carry forward reaccess_count at time of archiving for reference
        "reaccess_count": sub.get("reaccess_count", 0),
    }

    await db.assessment_version_history.insert_one(history_doc)
    return version


# ---------------------------------------------------------------------------
# Grant re-access with automatic archive
# ---------------------------------------------------------------------------


async def grant_reaccess_with_archive(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    admin_id: str,
    reason: str,
    reason_category: str,
) -> None:
    """Archive the current submission and reset it so the candidate can re-attempt.

    Steps:
    1. Guard against exceeding MAX_REACCESS_COUNT.
    2. Archive the current state via archive_submission().
    3. Re-sample questions from the assessment (fresh shuffle).
    4. Reset the submission document to a PENDING state.

    Args:
        db: AsyncIOMotorDatabase instance.
        submission_id: String ObjectId of the submission.
        admin_id: String ObjectId of the admin granting re-access.
        reason: Human-readable reason for the re-access grant.
        reason_category: One of ReaccessReasonCategory values.

    Raises:
        NotFoundException: If the submission or its assessment does not exist.
        ForbiddenException: If the submission has already reached MAX_REACCESS_COUNT.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException(_ERR_SUBMISSION_NOT_FOUND)

    if sub.get("reaccess_count", 0) >= settings.MAX_REACCESS_COUNT:
        raise ForbiddenException(
            f"Maximum re-access count ({settings.MAX_REACCESS_COUNT}) already reached."
        )

    _reaccess_eligible_statuses = {
        SubmissionStatus.COMPLETED,
        SubmissionStatus.MALPRACTICE,
        SubmissionStatus.TERMINATED,
    }
    if sub.get("status") not in _reaccess_eligible_statuses:
        raise ForbiddenException(
            f"Cannot grant re-access to a submission with status '{sub.get('status')}'. "
            "Only completed, malpractice, or terminated submissions are eligible."
        )

    # Archive current attempt and get the new version number
    new_version = await archive_submission(db, submission_id, admin_id, reason, reason_category)

    # Fetch assessment to re-sample questions
    assessment = await db.assessments.find_one({"_id": sub["assessment_id"], "is_active": True})
    if not assessment:
        raise NotFoundException("Assessment not found")

    # Re-build rounds_data with fresh question sampling
    from app.components.candidate.candidate_service import _build_round_data

    rounds_data = [
        await _build_round_data(db, round_cfg) for round_cfg in assessment.get("rounds", [])
    ]

    now = utcnow()
    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {
            "$set": {
                "status": SubmissionStatus.PENDING,
                "rounds_data": rounds_data,
                "current_round": 1,
                "malpractice_count": 0,
                "malpractice_events": [],
                "malpractice_data": [],
                "score": 0,
                "percentage": 0.0,
                "remaining_seconds": None,
                "current_question_idx": 0,
                "reaccess_count": new_version,
                "screenshots": [],
                "completed_at": None,
                "updated_at": now,
            }
        },
    )
