from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import QuestionType, SubmissionStatus
from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.utils import (
    build_pagination_meta,
    generate_sharelink,
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
    """Create a new assessment with rounds and a unique share link."""
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
        "share_link": generate_sharelink(workspace_id),
        "created_by": ObjectId(user_id),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.assessments.insert_one(doc)
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

    Raises:
        NotFoundException: If no assessment matches the share link.
    """
    doc = await db.assessments.find_one({"share_link": share_link})
    if not doc:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)
    safe = serialize_doc(doc)
    for r in safe.get("rounds", []):
        r.pop("question_ids", None)
    return safe


async def get_submissions(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    search: str | None,
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
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
        raise NotFoundException("Submission not found")

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
                "phone": "$candidate.candidate_data.phone",
                "percentage": 1,
                "status": 1,
                "completed_at": 1,
                "rounds_count": {"$size": "$rounds_data"},
            }
        }
    )

    docs = await db.assessment_submissions.aggregate(pipeline).to_list(10000)
    return serialize_docs(docs)
