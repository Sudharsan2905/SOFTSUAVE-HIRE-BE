import asyncio
import random
from pathlib import Path
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import QuestionType, SubmissionStatus
from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.utils import (
    build_pagination_meta,
    decode_sharelink,
    paginate_query,
    safe_regex,
    serialize_doc,
    serialize_docs,
    utcnow,
)
from app.core.config import settings
from app.core.logging import logger

_EXT_MAP = {"image/jpeg": ".jpg", "image/png": ".png"}

_ERR_ASSESSMENT_NOT_FOUND = "Assessment not found"
_ERR_ACTIVE_SUBMISSION_NOT_FOUND = "Active submission not found"

# MongoDB pipeline operator constants
_MATCH = "$match"
_REGEX = "$regex"
_OPTIONS = "$options"

# Use SystemRandom (backed by os.urandom) for question shuffling
_rng = random.SystemRandom()


async def get_candidate_assessment(db: AsyncIOMotorDatabase, share_link: str) -> dict:
    """Return assessment metadata for a candidate via share link, without internal question IDs.

    Tries to decode the link as a signed token first; falls back to a direct share_link
    field lookup for backward compatibility with legacy links.

    Raises:
        NotFoundException: If the share link is invalid.
    """
    from app.common.exceptions import ValidationException

    doc = None
    try:
        decoded = decode_sharelink(share_link)
        doc = await db.assessments.find_one({"_id": ObjectId(decoded["a"])})
    except (ValidationException, Exception):
        doc = await db.assessments.find_one({"share_link": share_link})

    if not doc:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)
    safe = serialize_doc(doc)
    for r in safe.get("rounds", []):
        r.pop("question_ids", None)
    return safe


async def _handle_existing_submission(db: AsyncIOMotorDatabase, existing: dict) -> dict:
    """Resume or raise for an existing submission.

    Raises:
        ForbiddenException: If the submission is in a terminal state (COMPLETED or MALPRACTICE).
    """
    status = existing.get("status")
    if status in [SubmissionStatus.COMPLETED, SubmissionStatus.MALPRACTICE]:
        raise ForbiddenException(
            "You have already completed this assessment. Please contact admin for re-access."
        )
    if status == SubmissionStatus.IN_PROGRESS:
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
    """Strip answer metadata and shuffle MCQ options for candidate-facing use."""
    sq = serialize_doc(q)
    q_type = sq.pop("question_type", "essay")
    if q_type in (QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI):
        opts = [{k: v for k, v in o.items() if k != "is_correct"} for o in sq.get("options", [])]
        _rng.shuffle(opts)
        sq["options"] = opts
    sq.pop("correct_answer", None)
    sq["text"] = sq.pop("question_text", "")
    sq["type"] = "mcq_multiple" if q_type == QuestionType.MCQ_MULTI else q_type
    return sq


async def _build_round_data(db: AsyncIOMotorDatabase, round_cfg: dict) -> dict:
    """Sample and sanitize questions for a single assessment round."""
    question_ids = round_cfg.get("question_ids", [])
    required = round_cfg["question_count"]
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

    - Returns existing submission if already IN_PROGRESS.
    - Transitions PENDING → IN_PROGRESS without re-sampling questions.
    - Creates a new submission (randomly sampling questions per round) on first call.

    Raises:
        NotFoundException: If the assessment share link is invalid.
        ForbiddenException: If the submission is already COMPLETED or MALPRACTICE.
    """
    assessment = None
    try:
        decoded = decode_sharelink(share_link)
        assessment = await db.assessments.find_one({"_id": ObjectId(decoded["a"])})
    except Exception:
        assessment = await db.assessments.find_one({"share_link": share_link})

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

    now = utcnow()
    submission = {
        "assessment_id": assessment["_id"],
        "candidate_id": ObjectId(candidate_id),
        "status": SubmissionStatus.IN_PROGRESS,
        "rounds_data": rounds_data,
        "current_round": 1,
        "score": 0,
        "percentage": 0.0,
        "screenshots": [],
        "is_malpractice": False,
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

    assessment = await db.assessments.find_one({"_id": sub["assessment_id"]})
    current = sub.get("current_round", 1)
    idx = current - 1
    rounds_data = sub.get("rounds_data", [])

    if idx >= len(rounds_data):
        raise NotFoundException("Round not found")

    rd = rounds_data[idx]
    tab_monitoring = False
    if assessment:
        monitoring = assessment.get("monitoring_config") or {}
        tab_monitoring = monitoring.get("tab_monitoring", False)

    return {
        "round": {
            "round_number": current,
            "questions": rd.get("questions", []),
            "max_duration_minutes": rd.get("max_duration_minutes", 30),
        },
        "tab_monitoring": tab_monitoring,
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
            "status": SubmissionStatus.IN_PROGRESS,
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
    """Mark the current round complete and advance to the next round or finalize the assessment.

    On the final round, calculates score/percentage and sets status to COMPLETED.

    Returns:
        {'completed': True, 'percentage': float} if all rounds are done,
        {'completed': False, 'next_round': int} otherwise.

    Raises:
        NotFoundException: If no active submission is found.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        raise NotFoundException(_ERR_ACTIVE_SUBMISSION_NOT_FOUND)

    assessment = await db.assessments.find_one({"_id": sub["assessment_id"]})
    if not assessment:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    current = sub.get("current_round", 1)
    total_rounds = len(assessment.get("rounds", []))
    idx = current - 1

    if current >= total_rounds:
        score, percentage = await _calculate_score(db, sub)
        await db.assessment_submissions.update_one(
            {"_id": sub["_id"]},
            {
                "$set": {
                    "status": SubmissionStatus.COMPLETED,
                    "score": score,
                    "percentage": percentage,
                    "completed_at": utcnow(),
                    "updated_at": utcnow(),
                    f"rounds_data.{idx}.completed": True,
                }
            },
        )
        logger.info(
            f"Assessment completed: submission_id={submission_id} "
            f"candidate_id={candidate_id} score={percentage:.1f}%"
        )
        return {"completed": True, "percentage": percentage}

    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {
            "$set": {
                "current_round": current + 1,
                "updated_at": utcnow(),
                f"rounds_data.{idx}.completed": True,
            }
        },
    )
    logger.info(
        f"Round {current} finished: submission_id={submission_id} "
        f"→ advancing to round {current + 1}"
    )
    return {"completed": False, "next_round": current + 1}


def _score_mcq(original: dict, given_answer: Any) -> int:
    """Return 1 if the candidate's MCQ answer exactly matches the correct option IDs, else 0."""
    correct_ids = {o["id"] for o in original.get("options", []) if o.get("is_correct")}
    given = [given_answer] if isinstance(given_answer, str) else (given_answer or [])
    return 1 if set(given) == correct_ids else 0


async def _calculate_score(db: AsyncIOMotorDatabase, submission: dict) -> tuple[int, float]:
    """Compute (correct_count, percentage) across all MCQ questions in a submission.

    Only MCQ questions (mcq_single / mcq_multiple) contribute to the total.
    Essay questions are excluded from both numerator and denominator so they
    do not deflate the percentage.

    Uses a single batched DB fetch for all question originals instead of
    one query per question.
    """
    mcq_pairs: list[tuple[str, Any]] = []  # (question_id, candidate_answer)
    for rd in submission.get("rounds_data", []):
        answers = rd.get("answers", {})
        for q in rd.get("questions", []):
            if q.get("type") in ("mcq_single", "mcq_multiple"):
                qid = q.get("id", "")
                mcq_pairs.append((qid, answers.get(qid, [])))

    if not mcq_pairs:
        return 0, 0.0

    qids = [ObjectId(qid) for qid, _ in mcq_pairs]
    originals = await db.questions.find({"_id": {"$in": qids}}).to_list(len(qids))
    original_map = {str(o["_id"]): o for o in originals}

    total = len(mcq_pairs)
    correct = sum(
        _score_mcq(original_map[qid], given) for qid, given in mcq_pairs if qid in original_map
    )
    pct = round((correct / total * 100) if total > 0 else 0.0, 2)
    return correct, pct


async def save_screenshot(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str,
    file_bytes: bytes,
    content_type: str = "image/jpeg",
) -> None:
    """Save a screenshot to disk and record its path in the submission document.

    Files are stored under SCREENSHOTS_DIR/{submission_id}/ with a UTC timestamp filename.
    Silently no-ops if the submission is not found.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        return

    round_number = sub.get("current_round", 1)
    ext = _EXT_MAP.get(content_type, ".jpg")
    timestamp = utcnow().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"round{round_number}_{timestamp}{ext}"

    folder = Path(settings.SCREENSHOTS_DIR) / submission_id
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / filename

    await asyncio.to_thread(file_path.write_bytes, file_bytes)

    relative_path = str(file_path).replace("\\", "/")
    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {
            "$push": {
                "screenshots": {
                    "path": relative_path,
                    "round": round_number,
                    "taken_at": utcnow(),
                }
            }
        },
    )
    logger.info(
        f"Screenshot saved: submission_id={submission_id} round={round_number} path={relative_path}"
    )


async def flag_malpractice(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str,
    malpractice_type: str,
) -> None:
    """Mark a submission as MALPRACTICE if tab monitoring is enabled; silently returns otherwise.

    Raises:
        NotFoundException: If no active in-progress submission exists.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        raise NotFoundException(_ERR_ACTIVE_SUBMISSION_NOT_FOUND)

    assessment = await db.assessments.find_one({"_id": sub["assessment_id"]})
    if assessment:
        monitoring_config = assessment.get("monitoring_config") or {}
        if not monitoring_config.get("tab_monitoring", True):
            return

    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {
            "$set": {
                "is_malpractice": True,
                "malpractice_reason": malpractice_type,
                "completed_at": utcnow(),
                "updated_at": utcnow(),
            }
        },
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
        {"$project": {"candidate.password_hash": 0, "rounds_data": 0}},
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
                        {"assessment.name": {_REGEX: escaped, _OPTIONS: "i"}},
                    ]
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
