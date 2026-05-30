from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import SubmissionStatus
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
        query["name"] = {"$regex": safe_regex(search), "$options": "i"}

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
        raise NotFoundException("Assessment not found")
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
        raise NotFoundException("Assessment not found")

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
        raise NotFoundException("Assessment not found")
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
        {"$match": {"assessment_id": ObjectId(assessment_id)}},
        {
            "$lookup": {
                "from": "users",
                "localField": "candidate_id",
                "foreignField": "_id",
                "as": "candidate",
            }
        },
        {"$unwind": {"path": "$candidate", "preserveNullAndEmpty": True}},
        {"$project": {"candidate.password_hash": 0, "rounds_data.questions": 0}},
    ]
    if search:
        escaped = safe_regex(search)
        pipeline.append(
            {
                "$match": {
                    "$or": [
                        {"candidate.first_name": {"$regex": escaped, "$options": "i"}},
                        {"candidate.last_name": {"$regex": escaped, "$options": "i"}},
                        {"candidate.email": {"$regex": escaped, "$options": "i"}},
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


async def get_submission_detail(db: AsyncIOMotorDatabase, submission_id: str) -> dict:
    """Fetch a single submission with the joined candidate user document.

    Raises:
        NotFoundException: If the submission does not exist.
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException("Submission not found")

    candidate = await db.users.find_one({"_id": sub["candidate_id"]}, {"password_hash": 0})
    result = serialize_doc(sub)
    result["candidate"] = serialize_doc(candidate)
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
        {"$match": {"assessment_id": ObjectId(assessment_id)}},
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
            "$lookup": {
                "from": "candidates",
                "localField": "candidate_id",
                "foreignField": "user_id",
                "as": "profile",
            }
        },
        {"$unwind": {"path": "$profile", "preserveNullAndEmpty": True}},
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
                "phone": "$profile.phone",
                "percentage": 1,
                "status": 1,
                "completed_at": 1,
                "rounds_count": {"$size": "$rounds_data"},
            }
        },
    ]
    docs = await db.assessment_submissions.aggregate(pipeline).to_list(10000)
    return serialize_docs(docs)
