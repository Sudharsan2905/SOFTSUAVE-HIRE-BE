"""Candidate-specific scheduling service.

Allows admins to schedule an individual candidate for an assessment with:
  - per-candidate monitoring overrides  (subset of MonitoringConfig)
  - per-round question selection         (replaces the random pool sample)
  - optional time-bounded share link
"""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.exceptions import NotFoundException, ValidationException
from app.common.utils import (
    encode_schedule_sharelink,
    serialize_doc,
    serialize_docs,
    utcnow,
)
from app.core.logging import logger

_ERR_ASSESSMENT_NOT_FOUND = "Assessment not found"
_ERR_CANDIDATE_NOT_FOUND = "Candidate not found"
_ERR_SCHEDULE_NOT_FOUND = "Schedule not found"


def _resolve_effective_monitoring(
    assessment_monitoring: dict | None,
    overrides: dict | None,
) -> dict:
    """Merge assessment-level defaults with candidate-level overrides.

    Only override keys that are explicitly set (non-None) in overrides.
    """
    base: dict = assessment_monitoring or {}
    if not overrides:
        return base
    merged = {**base}
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return merged


async def schedule_candidate(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    workspace_id: str,
    data: dict,
    created_by: str,
) -> dict:
    """Create a candidate-specific schedule with optional monitoring overrides
    and question selection.

    Generates a dedicated share link that embeds the schedule_id so the candidate
    interview flow can resolve overrides at runtime.

    Args:
        data: Validated dict from ScheduleCandidateRequest.model_dump().

    Returns:
        The created schedule document (serialised).

    Raises:
        NotFoundException: If assessment or candidate does not exist.
        ValidationException: If round numbers are out of bounds or question counts insufficient.
    """
    assessment = await db.assessments.find_one(
        {"_id": ObjectId(assessment_id), "workspace_id": ObjectId(workspace_id)}
    )
    if not assessment:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    candidate_id = data["candidate_id"]
    candidate = await db.users.find_one({"_id": ObjectId(candidate_id)})
    if not candidate:
        raise NotFoundException(_ERR_CANDIDATE_NOT_FOUND)

    assessment_rounds: list[dict] = assessment.get("rounds", [])

    # Validate and build per-round question override
    scheduled_rounds: list[dict] | None = None
    if data.get("rounds"):
        scheduled_rounds = []
        round_map = {r["round_number"]: r for r in assessment_rounds}
        for sr in data["rounds"]:
            rn = sr["round_number"]
            if rn not in round_map:
                raise ValidationException(f"Round {rn} does not exist in the assessment")
            required = round_map[rn]["question_count"]
            given = sr.get("question_ids", [])
            if len(given) < required:
                raise ValidationException(
                    f"Round {rn} requires at least {required} question(s); "
                    f"only {len(given)} provided"
                )
            scheduled_rounds.append(
                {
                    "round_number": rn,
                    "question_ids": [ObjectId(qid) for qid in given],
                }
            )

    # Normalise monitoring overrides — strip None values
    overrides_raw: dict | None = None
    if data.get("monitoring_overrides"):
        mo = data["monitoring_overrides"]
        overrides_raw = {k: v for k, v in mo.items() if v is not None}
        if not overrides_raw:
            overrides_raw = None

    start_time: str | None = data.get("start_time")
    end_time: str | None = data.get("end_time")

    # Validate ISO 8601 timestamps when provided
    if start_time and end_time:
        from datetime import UTC
        from datetime import datetime as _dt

        try:
            s = _dt.fromisoformat(start_time).replace(tzinfo=UTC)
            e = _dt.fromisoformat(end_time).replace(tzinfo=UTC)
        except ValueError as err:
            raise ValidationException("Invalid date format. Use ISO 8601.") from err
        if e <= s:
            raise ValidationException("end_time must be after start_time")

    now = utcnow()
    doc: dict = {
        "assessment_id": ObjectId(assessment_id),
        "workspace_id": ObjectId(workspace_id),
        "candidate_id": ObjectId(candidate_id),
        "monitoring_overrides": overrides_raw,
        "rounds": scheduled_rounds,
        "start_time": start_time,
        "end_time": end_time,
        "created_by": ObjectId(created_by),
        "created_at": now,
        "updated_at": now,
    }

    result = await db.candidate_schedules.insert_one(doc)
    schedule_id = str(result.inserted_id)

    share_link = encode_schedule_sharelink(assessment_id, schedule_id, start_time, end_time)
    await db.candidate_schedules.update_one(
        {"_id": result.inserted_id}, {"$set": {"share_link": share_link}}
    )
    doc["share_link"] = share_link
    doc["_id"] = result.inserted_id

    logger.info(
        f"Candidate scheduled: assessment_id={assessment_id} "
        f"candidate_id={candidate_id} schedule_id={schedule_id}"
    )
    return serialize_doc(doc)


async def get_schedules(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    workspace_id: str,
) -> list:
    """Return all candidate schedules for an assessment, joined with candidate user data."""
    pipeline = [
        {
            "$match": {
                "assessment_id": ObjectId(assessment_id),
                "workspace_id": ObjectId(workspace_id),
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "candidate_id",
                "foreignField": "_id",
                "as": "candidate",
            }
        },
        {"$unwind": {"path": "$candidate", "preserveNullAndEmptyArrays": True}},
        {"$project": {"candidate.password_hash": 0}},
        {"$sort": {"created_at": -1}},
    ]
    docs = await db.candidate_schedules.aggregate(pipeline).to_list(1000)  # type: ignore[arg-type]
    return serialize_docs(docs)


async def get_schedule(
    db: AsyncIOMotorDatabase,
    schedule_id: str,
    workspace_id: str,
) -> dict:
    """Return a single schedule document with resolved effective monitoring config.

    Raises:
        NotFoundException: If the schedule does not exist or belongs to another workspace.
    """
    doc = await db.candidate_schedules.find_one(
        {"_id": ObjectId(schedule_id), "workspace_id": ObjectId(workspace_id)}
    )
    if not doc:
        raise NotFoundException(_ERR_SCHEDULE_NOT_FOUND)

    assessment = await db.assessments.find_one({"_id": doc["assessment_id"]})
    assessment_monitoring = assessment.get("monitoring_config") if assessment else None

    result = serialize_doc(doc)
    result["effective_monitoring"] = _resolve_effective_monitoring(
        assessment_monitoring, doc.get("monitoring_overrides")
    )
    return result


async def get_schedule_by_id(db: AsyncIOMotorDatabase, schedule_id: str) -> dict | None:
    """Internal helper: fetch a raw schedule document by _id (no workspace check)."""
    doc = await db.candidate_schedules.find_one({"_id": ObjectId(schedule_id)})
    return doc
