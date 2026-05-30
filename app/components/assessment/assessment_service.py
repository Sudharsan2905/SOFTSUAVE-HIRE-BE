from datetime import UTC
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import QuestionType, SubmissionStatus
from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.utils import (
    build_pagination_meta,
    decode_sharelink,
    encode_permanent_sharelink,
    generate_sharelink,  # noqa: F401 — kept for backward compatibility
    list_paginated,
    paginate_query,
    safe_regex,
    serialize_doc,
    serialize_docs,
    utcnow,
)
from app.core.config import settings
from app.core.logging import logger

_ERR_ASSESSMENT_NOT_FOUND = "Assessment not found"
_MATCH = "$match"
_LOOKUP = "$lookup"
_UNWIND = "$unwind"
_REGEX = "$regex"
_OPTIONS = "$options"


def _build_rounds(rounds_data: list) -> list:
    result = []
    for r in rounds_data:
        if hasattr(r, "model_dump"):
            r = r.model_dump()
        result.append(
            {
                "round_number": r["round_number"],
                "question_count": r["question_count"],
                "max_duration_minutes": r["max_duration_minutes"],
                "question_ids": [ObjectId(qid) for qid in r.get("question_ids", [])],
            }
        )
    return result


async def create_assessment(
    db: AsyncIOMotorDatabase, workspace_id: str, data: dict, user_id: str
) -> dict:
    """Create a new assessment with rounds and a unique tamper-proof share link."""
    monitoring: Any = data.get("monitoring_config")
    if monitoring is not None and hasattr(monitoring, "model_dump"):
        monitoring = monitoring.model_dump()

    now = utcnow()
    doc = {
        "workspace_id": ObjectId(workspace_id),
        "name": data["name"],
        "description": data.get("description", ""),
        "rounds": _build_rounds(data.get("rounds", [])),
        "accessibility": data["accessibility"],
        "monitoring_config": monitoring,
        "created_by": ObjectId(user_id),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.assessments.insert_one(doc)
    assessment_id = str(result.inserted_id)
    share_link = encode_permanent_sharelink(assessment_id)
    await db.assessments.update_one(
        {"_id": result.inserted_id}, {"$set": {"share_link": share_link}}
    )
    doc["share_link"] = share_link
    doc["_id"] = result.inserted_id
    logger.info(f"Assessment created: '{data['name']}' in workspace_id={workspace_id}")
    return serialize_doc(doc)


async def get_assessments(
    db: AsyncIOMotorDatabase,
    workspace_id: str,
    search: str | None,
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
) -> dict:
    """Return a paginated list of assessments in a workspace, with submission counts."""
    skip, limit = paginate_query(page, page_size)
    query: dict = {"workspace_id": ObjectId(workspace_id)}
    if search:
        query["name"] = {_REGEX: safe_regex(search), _OPTIONS: "i"}

    sort_dir = 1 if sort_order == "asc" else -1
    total, docs = await list_paginated(
        db.assessments,
        query,
        sort_by,
        sort_dir,
        skip,
        limit,
        ["name", "created_at", "updated_at"],
    )

    for doc in docs:
        doc["submission_count"] = await db.assessment_submissions.count_documents(
            {"assessment_id": doc["_id"]}
        )

    return {
        "assessments": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }


async def get_assessment(db: AsyncIOMotorDatabase, workspace_id: str, assessment_id: str) -> dict:
    """Fetch a single assessment by workspace and assessment ID.

    Raises:
        NotFoundException: If the assessment does not exist in the workspace.
    """
    doc = await db.assessments.find_one(
        {"_id": ObjectId(assessment_id), "workspace_id": ObjectId(workspace_id)}
    )
    if not doc:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)
    return serialize_doc(doc)


async def update_assessment(
    db: AsyncIOMotorDatabase, workspace_id: str, assessment_id: str, data: dict
) -> dict:
    """Update an assessment's name, description, rounds, accessibility, or monitoring config.

    Raises:
        NotFoundException: If the assessment does not exist in the workspace.
    """
    if not await db.assessments.find_one(
        {"_id": ObjectId(assessment_id), "workspace_id": ObjectId(workspace_id)}
    ):
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    update: dict = {"updated_at": utcnow()}
    if data.get("name"):
        update["name"] = data["name"]
    if data.get("description") is not None:
        update["description"] = data["description"]
    if data.get("rounds"):
        update["rounds"] = _build_rounds(data["rounds"])
    if data.get("accessibility"):
        update["accessibility"] = data["accessibility"]
    if data.get("monitoring_config"):
        mc = data["monitoring_config"]
        update["monitoring_config"] = mc.model_dump() if hasattr(mc, "model_dump") else mc

    await db.assessments.update_one({"_id": ObjectId(assessment_id)}, {"$set": update})
    return serialize_doc(await db.assessments.find_one({"_id": ObjectId(assessment_id)}))


async def get_assessment_by_share_link(db: AsyncIOMotorDatabase, share_link: str) -> dict:
    """Fetch an assessment by its public share link, stripping internal question_ids from rounds.

    Tries to decode the link as a signed token first; falls back to a direct share_link
    field lookup for backward compatibility with legacy links.

    Raises:
        NotFoundException: If no assessment matches the share link.
    """
    from app.common.exceptions import ValidationException

    doc = None
    try:
        decoded = decode_sharelink(share_link)
        assessment_id = decoded["a"]
        doc = await db.assessments.find_one({"_id": ObjectId(assessment_id)})
    except (ValidationException, Exception):
        doc = await db.assessments.find_one({"share_link": share_link})

    if not doc:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)
    safe = serialize_doc(doc)
    for r in safe.get("rounds", []):
        r.pop("question_ids", None)
    return safe


async def validate_sharelink(db: AsyncIOMotorDatabase, encoded_link: str) -> dict:
    """Validate a permanent or expirable share link.

    Returns dict with: assessment_id, is_expirable, is_expired, can_allow,
    start_time, end_time, message
    """
    from app.common.exceptions import ValidationException

    try:
        decoded = decode_sharelink(encoded_link)
    except ValidationException:
        return {
            "can_allow": False,
            "is_expired": True,
            "is_expirable": False,
            "start_time": None,
            "end_time": None,
            "message": "This share link is invalid.",
        }

    assessment_id = decoded["a"]
    try:
        doc = await db.assessments.find_one({"_id": ObjectId(assessment_id)}, {"_id": 1})
    except Exception:
        doc = None

    if not doc:
        return {
            "can_allow": False,
            "is_expired": True,
            "is_expirable": False,
            "start_time": None,
            "end_time": None,
            "message": "This share link is invalid.",
        }

    # Permanent link — always allowed
    if "s" not in decoded:
        return {
            "can_allow": True,
            "is_expired": False,
            "is_expirable": False,
            "start_time": None,
            "end_time": None,
            "message": "You may proceed to attend the interview.",
        }

    # Expirable link
    from datetime import datetime as _dt

    now = utcnow().replace(tzinfo=UTC)
    start = _dt.fromisoformat(decoded["s"]).replace(tzinfo=UTC)
    end = _dt.fromisoformat(decoded["e"]).replace(tzinfo=UTC)

    if now < start:
        return {
            "can_allow": False,
            "is_expired": False,
            "is_expirable": True,
            "start_time": decoded["s"],
            "end_time": decoded["e"],
            "message": "Your interview link is not active yet.",
        }
    if now > end:
        return {
            "can_allow": False,
            "is_expired": True,
            "is_expirable": True,
            "start_time": decoded["s"],
            "end_time": decoded["e"],
            "message": "This interview link has expired.",
        }
    return {
        "can_allow": True,
        "is_expired": False,
        "is_expirable": True,
        "start_time": decoded["s"],
        "end_time": decoded["e"],
        "message": "You may proceed to attend the interview.",
    }


async def generate_expirable_link(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    workspace_id: str,
    start_iso: str,
    end_iso: str,
) -> str:
    """Generate an expirable share link for a given assessment."""
    from datetime import datetime as _dt

    from app.common.exceptions import NotFoundException, ValidationException
    from app.common.utils import encode_expirable_sharelink

    doc = await db.assessments.find_one(
        {"_id": ObjectId(assessment_id), "workspace_id": ObjectId(workspace_id)}, {"_id": 1}
    )
    if not doc:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    try:
        start = _dt.fromisoformat(start_iso).replace(tzinfo=UTC)
        end = _dt.fromisoformat(end_iso).replace(tzinfo=UTC)
    except ValueError as err:
        raise ValidationException("Invalid date format. Use ISO 8601.") from err

    if end <= start:
        raise ValidationException("End time must be after start time.")

    return encode_expirable_sharelink(assessment_id, start_iso, end_iso)


async def get_submissions(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    search: str | None,
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Return paginated submissions for an assessment, joined with candidate user data.

    Uses an aggregation pipeline so candidate name/email can be searched.
    Question bodies are excluded from the response for performance.
    """
    skip, limit = paginate_query(page, page_size)
    sort_dir = 1 if sort_order == "asc" else -1
    sort_field = (
        sort_by
        if sort_by in ["percentage", "created_at", "updated_at", "completed_at"]
        else "created_at"
    )

    pipeline: list[Any] = [
        {_MATCH: {"assessment_id": ObjectId(assessment_id)}},
        {
            _LOOKUP: {
                "from": "users",
                "localField": "candidate_id",
                "foreignField": "_id",
                "as": "candidate",
            }
        },
        {_UNWIND: {"path": "$candidate", "preserveNullAndEmptyArrays": True}},
        {"$project": {"candidate.password_hash": 0, "rounds_data.questions": 0}},
    ]
    if search:
        escaped = safe_regex(search)
        pipeline.append(
            {
                _MATCH: {
                    "$or": [
                        {"candidate.first_name": {_REGEX: escaped, _OPTIONS: "i"}},
                        {"candidate.last_name": {_REGEX: escaped, _OPTIONS: "i"}},
                        {"candidate.email": {_REGEX: escaped, _OPTIONS: "i"}},
                    ]
                }
            }
        )

    from datetime import datetime as _dt

    date_match: dict = {}
    if from_date:
        try:
            date_match["$gte"] = _dt.fromisoformat(from_date)
        except ValueError:
            pass
    if to_date:
        try:
            end_dt = _dt.fromisoformat(to_date).replace(hour=23, minute=59, second=59)
            date_match["$lte"] = end_dt
        except ValueError:
            pass
    if date_match:
        pipeline.append({"$match": {"started_at": date_match}})

    from datetime import datetime as _dt

    date_match: dict = {}
    if from_date:
        try:
            date_match["$gte"] = _dt.fromisoformat(from_date)
        except ValueError:
            pass
    if to_date:
        try:
            end_dt = _dt.fromisoformat(to_date).replace(hour=23, minute=59, second=59)
            date_match["$lte"] = end_dt
        except ValueError:
            pass
    if date_match:
        pipeline.append({"$match": {"started_at": date_match}})

    count_res = await db.assessment_submissions.aggregate(pipeline + [{"$count": "total"}]).to_list(
        1
    )
    total = count_res[0]["total"] if count_res else 0

    pipeline += [{"$sort": {sort_field: sort_dir}}, {"$skip": skip}, {"$limit": limit}]
    docs = await db.assessment_submissions.aggregate(pipeline).to_list(limit)

    return {
        "submissions": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }


async def _enrich_question(db: AsyncIOMotorDatabase, q: dict, answers: dict) -> None:
    """Augment a question dict in-place with correct-answer and candidate-answer data."""
    qid = q["id"]
    raw_answer = answers.get(qid, [])
    candidate_answer = [raw_answer] if isinstance(raw_answer, str) else raw_answer

    original = await db.questions.find_one({"_id": ObjectId(qid)})
    if original and original.get("question_type") in (
        QuestionType.MCQ_SINGLE,
        QuestionType.MCQ_MULTI,
    ):
        correct_ids = [str(o["id"]) for o in original.get("options", []) if o.get("is_correct")]
        q["correct_option_ids"] = correct_ids
        q["is_correct"] = bool(candidate_answer) and set(candidate_answer) == set(correct_ids)
    else:
        q["correct_option_ids"] = []
        q["is_correct"] = None

    q["candidate_answer"] = candidate_answer


async def get_submission_detail(db: AsyncIOMotorDatabase, submission_id: str) -> dict:
    """Fetch a single submission enriched with correct-answer data for admin review.

    Each question in rounds_data is augmented with:
    - candidate_answer: list of option IDs (MCQ) or essay text string the candidate submitted
    - correct_option_ids: list of option IDs marked correct in the question bank (MCQ only)
    - is_correct: True/False for MCQ questions, None for essay

    Raises:
        NotFoundException: If the submission does not exist.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException("Submission not found")

    candidate = await db.users.find_one({"_id": sub["candidate_id"]}, {"password_hash": 0})
    result = serialize_doc(sub)
    result["candidate"] = serialize_doc(candidate)

    for rd in result.get("rounds_data", []):
        answers = rd.get("answers", {})
        for q in rd.get("questions", []):
            await _enrich_question(db, q, answers)

    return result


async def grant_reaccess(db: AsyncIOMotorDatabase, submission_id: str) -> None:
    """Reset a completed/malpractice submission to pending so the candidate can retry.

    Raises:
        NotFoundException: If the submission does not exist.
        ForbiddenException: If MAX_REACCESS_COUNT has been reached.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException("Submission not found")
    if sub.get("reaccess_count", 0) >= settings.MAX_REACCESS_COUNT:
        raise ForbiddenException(
            f"Maximum re-access limit of {settings.MAX_REACCESS_COUNT} has been reached"
        )
    logger.info(f"Re-access granted for submission_id={submission_id}")
    await db.assessment_submissions.update_one(
        {"_id": ObjectId(submission_id)},
        {
            "$set": {"status": SubmissionStatus.PENDING, "updated_at": utcnow()},
            "$inc": {"reaccess_count": 1},
        },
    )


async def export_submissions(db: AsyncIOMotorDatabase, assessment_id: str) -> list:
    """Return all submissions for an assessment in a flat format suitable for Excel export."""
    pipeline: list[Any] = [
        {_MATCH: {"assessment_id": ObjectId(assessment_id)}},
        {
            _LOOKUP: {
                "from": "users",
                "localField": "candidate_id",
                "foreignField": "_id",
                "as": "candidate",
            }
        },
        {_UNWIND: "$candidate"},
        {
            "$project": {
                "name": {
                    "$concat": [
                        "$candidate.first_name",
                        " ",
                        {"$ifNull": ["$candidate.last_name", ""]},
                    ]
                },
                "email": "$candidate.email",
                "phone": "$candidate.candidate_data.phone",
                "percentage": 1,
                "status": 1,
                "completed_at": 1,
                "rounds_count": {"$size": "$rounds_data"},
            }
        },
    ]
    docs = await db.assessment_submissions.aggregate(pipeline).to_list(10000)
    return serialize_docs(docs)
