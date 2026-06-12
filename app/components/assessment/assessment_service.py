from datetime import UTC
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import QuestionType, SubmissionStatus
from app.common.constants.messages import ErrorMessages
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
from app.core.logging import logger

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
        "is_active": True,
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
    query: dict = {"workspace_id": ObjectId(workspace_id), "is_active": True}
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
        {"_id": ObjectId(assessment_id), "workspace_id": ObjectId(workspace_id), "is_active": True}
    )
    if not doc:
        raise NotFoundException(ErrorMessages.ASSESSMENT_NOT_FOUND)
    return serialize_doc(doc)


async def update_assessment(
    db: AsyncIOMotorDatabase, workspace_id: str, assessment_id: str, data: dict
) -> dict:
    """Update an assessment's name, description, rounds, accessibility, or monitoring config.

    Raises:
        NotFoundException: If the assessment does not exist in the workspace.
    """
    if not await db.assessments.find_one(
        {"_id": ObjectId(assessment_id), "workspace_id": ObjectId(workspace_id), "is_active": True}
    ):
        raise NotFoundException(ErrorMessages.ASSESSMENT_NOT_FOUND)

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
    return serialize_doc(
        await db.assessments.find_one({"_id": ObjectId(assessment_id), "is_active": True})
    )


async def delete_assessment(
    db: AsyncIOMotorDatabase, workspace_id: str, assessment_id: str
) -> None:
    """Soft-delete an assessment by setting is_active=False.

    Raises:
        NotFoundException: If the assessment does not exist or is already deleted.
    """
    result = await db.assessments.update_one(
        {
            "_id": ObjectId(assessment_id),
            "workspace_id": ObjectId(workspace_id),
            "is_active": True,
        },
        {"$set": {"is_active": False, "updated_at": utcnow()}},
    )
    if result.matched_count == 0:
        raise NotFoundException(ErrorMessages.ASSESSMENT_NOT_FOUND)
    logger.info(f"Assessment soft-deleted: assessment_id={assessment_id}")


async def validate_sharelink(db: AsyncIOMotorDatabase, encoded_link: str) -> dict:
    """Validate a share link.

    Returns dict with: is_expirable, is_expired, can_allow,
    start_time, end_time, message.

    Handles three link formats:
    * New unified links (payload has ``"n"`` key) — always resolved via
      assessment_shares; time bounds read from the stored document.
    """
    from app.common.exceptions import ValidationException

    try:
        decoded = decode_sharelink(encoded_link)
    except ValidationException:
        return {
            "can_allow": False,
            "is_expired": False,
            "is_expirable": False,
            "start_time": None,
            "end_time": None,
            "message": ErrorMessages.SHARE_LINK_NOT_VALID,
        }

    assessment_id = decoded["a"]
    try:
        doc = await db.assessments.find_one(
            {"_id": ObjectId(assessment_id), "is_active": True}, {"_id": 1}
        )
    except Exception:
        doc = None

    if not doc:
        return {
            "can_allow": False,
            "is_expired": True,
            "is_expirable": False,
            "start_time": None,
            "end_time": None,
            "message": ErrorMessages.SHARE_LINK_NOT_VALID,
        }

    # New unified share link (nonce present) — always resolved via assessment_shares.
    if "n" in decoded:
        share_doc = await db.assessment_shares.find_one(
            {"share_link": encoded_link, "is_active": True},
            {"_id": 1, "start_time": 1, "end_time": 1},
        )
        if not share_doc:
            return {
                "can_allow": False,
                "is_expired": True,
                "is_expirable": False,
                "start_time": None,
                "end_time": None,
                "message": ErrorMessages.SHARE_LINK_REVOKED_CONTACT,
            }

        start = share_doc.get("start_time")
        end = share_doc.get("end_time")

        if start and end:
            from datetime import datetime as _dt

            now = utcnow().replace(tzinfo=UTC)
            start_dt = _dt.fromisoformat(start).replace(tzinfo=UTC)
            end_dt = _dt.fromisoformat(end).replace(tzinfo=UTC)

            if now < start_dt:
                return {
                    "can_allow": False,
                    "is_expired": False,
                    "is_expirable": True,
                    "start_time": start,
                    "end_time": end,
                    "message": ErrorMessages.SHARE_LINK_NOT_ACTIVE,
                }
            if now > end_dt:
                return {
                    "can_allow": False,
                    "is_expired": True,
                    "is_expirable": True,
                    "start_time": start,
                    "end_time": end,
                    "message": ErrorMessages.SHARE_LINK_SESSION_UNAVAILABLE,
                }
            return {
                "can_allow": True,
                "is_expired": False,
                "is_expirable": True,
                "start_time": start,
                "end_time": end,
                "message": ErrorMessages.ALLOW_TO_INTERVIEW,
            }

        return {
            "can_allow": True,
            "is_expired": False,
            "is_expirable": False,
            "start_time": None,
            "end_time": None,
            "message": ErrorMessages.ALLOW_TO_INTERVIEW,
        }

    return {
        "can_allow": True,
        "is_expired": False,
        "is_expirable": False,
        "start_time": None,
        "end_time": None,
        "message": ErrorMessages.ALLOW_TO_INTERVIEW,
    }


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


def _enrich_question_from_map(q: dict, answers: dict, original_map: dict) -> None:
    """Augment a question dict in-place using a pre-fetched map of original question documents."""
    qid = q["id"]
    raw_answer = answers.get(qid, [])
    candidate_answer = [raw_answer] if isinstance(raw_answer, str) else raw_answer

    original = original_map.get(qid)
    mcq_types = (QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI)
    if original and original.get("question_type") in mcq_types:
        correct_ids = [str(o["id"]) for o in original.get("options", []) if o.get("is_correct")]
        q["correct_option_ids"] = correct_ids
        q["is_correct"] = bool(candidate_answer) and set(candidate_answer) == set(correct_ids)
    else:
        q["correct_option_ids"] = []
        q["is_correct"] = None

    if original and not q.get("options"):
        q["options"] = [
            {
                "id": str(o.get("id", "")),
                "text": o.get("text", ""),
                "is_correct": o.get("is_correct", False),
            }
            for o in original.get("options", [])
        ]
    if original and not q.get("question_text"):
        q["question_text"] = original.get("question_text", "")
    if original and not q.get("question_type"):
        q["question_type"] = original.get("question_type", "")

    q["candidate_answer"] = candidate_answer


async def get_submission_detail(db: AsyncIOMotorDatabase, submission_id: str) -> dict:
    """Fetch a single submission enriched with correct-answer data for admin review.

    Each question in rounds_data is augmented with:
    - candidate_answer: list of option IDs (MCQ) or essay text string the candidate submitted
    - correct_option_ids: list of option IDs marked correct in the question bank (MCQ only)
    - is_correct: True/False for MCQ questions, None for essay

    Uses a single batched DB fetch for all question originals (3 total DB calls regardless
    of question count).

    Raises:
        NotFoundException: If the submission does not exist.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)

    candidate = await db.users.find_one({"_id": sub["candidate_id"]}, {"password_hash": 0})
    result = serialize_doc(sub)
    result["candidate"] = serialize_doc(candidate)

    all_qids = [
        ObjectId(q["id"])
        for rd in result.get("rounds_data", [])
        for q in rd.get("questions", [])
        if q.get("id")
    ]
    if all_qids:
        originals = await db.questions.find({"_id": {"$in": all_qids}}).to_list(len(all_qids))
        original_map = {str(o["_id"]): o for o in originals}
    else:
        original_map = {}

    for rd in result.get("rounds_data", []):
        answers = rd.get("answers", {})
        for q in rd.get("questions", []):
            _enrich_question_from_map(q, answers, original_map)

    return result


async def grant_reaccess(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    admin_id: str = "",
    reason: str = "",
    reason_category: str = "other",
) -> None:
    """Archive current attempt and reset submission to PENDING with reshuffled questions.

    Raises:
        NotFoundException: If the submission does not exist.
        ForbiddenException: If MAX_REACCESS_COUNT has been reached.
    """
    from app.components.version_history import version_service

    await version_service.grant_reaccess_with_archive(
        db, submission_id, admin_id, reason, reason_category
    )
    logger.info(f"Re-access granted with archive: submission_id={submission_id}")


async def admin_resume_interview(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    admin_id: str,
) -> None:
    """Resume an ON_HOLD session and push a WebSocket event to the candidate if online.

    Transitions:  ON_HOLD → IN_PROGRESS

    Raises:
        NotFoundException: If the submission is not found.
        ForbiddenException: If the submission is not currently ON_HOLD.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)
    if sub.get("status") != SubmissionStatus.ON_HOLD:
        raise ForbiddenException(
            f"Cannot resume a submission with status '{sub.get('status')}'. "
            "Only ON_HOLD submissions can be resumed."
        )

    now = utcnow()
    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {
            "$set": {
                "status": SubmissionStatus.IN_PROGRESS,
                "resumed_at": now,
                "updated_at": now,
            }
        },
    )
    logger.info("Interview resumed: submission_id=%s admin_id=%s", submission_id, admin_id)

    try:
        from app.components.websocket.connection_manager import manager

        await manager.send_json(
            submission_id,
            {
                "type": "resume_approved",
                "remaining_seconds": sub.get("remaining_seconds"),
                "current_question_idx": sub.get("current_question_idx", 0),
            },
        )
    except Exception as exc:
        logger.warning("Could not push resume_approved WS event: %s", exc)


async def export_submissions(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    status: str | None = None,
    search: str | None = None,
    min_percentage: float | None = None,
    max_percentage: float | None = None,
) -> list:
    """Return filtered submissions for an assessment in a flat format suitable for Excel export.

    Filters: status, search (name/email), min_percentage, max_percentage.
    """
    match_query: dict = {"assessment_id": ObjectId(assessment_id)}
    if status:
        match_query["status"] = status
    if min_percentage is not None or max_percentage is not None:
        pct_filter: dict = {}
        if min_percentage is not None:
            pct_filter["$gte"] = min_percentage
        if max_percentage is not None:
            pct_filter["$lte"] = max_percentage
        match_query["percentage"] = pct_filter

    pipeline: list[Any] = [
        {_MATCH: match_query},
        {
            _LOOKUP: {
                "from": "users",
                "localField": "candidate_id",
                "foreignField": "_id",
                "as": "candidate",
            }
        },
        {_UNWIND: "$candidate"},
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

    pipeline.append(
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
                "phone": "$candidate.phone",
                "percentage": 1,
                "status": 1,
                "completed_at": 1,
                "rounds": {
                    "$map": {
                        "input": "$rounds_data",
                        "as": "rd",
                        "in": {
                            "round_number": "$$rd.round_number",
                            "percentage": {"$ifNull": ["$$rd.percentage", 0]},
                        },
                    }
                },
            }
        }
    )

    docs = await db.assessment_submissions.aggregate(pipeline).to_list(10000)
    return serialize_docs(docs)
