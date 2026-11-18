"""Assessment agent (deterministic): category + questions + ONE assessment + restricted share link."""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import AssessmentAccessibility
from app.common.utils import safe_regex, utcnow
from app.components.assessment import assessment_service, share_service
from app.core.config import settings
from app.core.logging import logger

MONITORING_CONFIG = {
    "tab_monitoring": True,
    "audio_monitoring": True,
    "video_monitoring": True,
    "screenshot_enabled": True,
    "screenshot_mode": "time_interval",
    "screenshot_interval_seconds": 30,
    "screenshot_count": None,
}


def essays_without_answer(questions: list[dict]) -> list[str]:
    return [
        q["question_text"][:60]
        for q in questions
        if q["question_type"] == "essay" and not (q.get("correct_answer") or "").strip()
    ]


async def find_or_create_category(
    db: AsyncIOMotorDatabase, jd_title: str, user_id: str
) -> ObjectId:
    """Question category named after the JD title (case-insensitive find-or-create)."""
    name = jd_title.strip()[:200]
    existing = await db.question_categories.find_one(
        {"name": {"$regex": f"^{safe_regex(name)}$", "$options": "i"}}
    )
    if existing:
        return existing["_id"]  # type: ignore[no-any-return]
    now = utcnow()
    doc = {
        "name": name,
        "description": f"AI-generated questions for JD: {name}",
        "created_by": ObjectId(user_id),
        "question_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    return (await db.question_categories.insert_one(doc)).inserted_id  # type: ignore[no-any-return]


def _question_doc(q: dict, category_id: ObjectId, user_id: str, now: object) -> dict:
    is_essay = q["question_type"] == "essay"
    return {
        "category_id": category_id,
        "question_text": q["question_text"],
        "question_type": q["question_type"],
        "complexity": q["complexity"],
        "image_key": None,
        "options": [] if is_essay else (q.get("options") or []),  # never null
        "correct_answer": q.get("correct_answer") if is_essay else None,
        "created_by": ObjectId(user_id),
        "created_at": now,
        "updated_at": now,
    }


async def build_assessment(
    db: AsyncIOMotorDatabase,
    *,
    workspace_id: str,
    user_id: str,
    jd_title: str,
    questions: list[dict],
    candidate_emails: list[str],
    duration_minutes: int | None = None,
) -> dict:
    """Create the category, insert questions, one monitored assessment and one restricted share."""
    missing = essays_without_answer(questions)
    if missing:
        raise ValueError(f"Essay questions without a model answer: {missing}")
    category_id = await find_or_create_category(db, jd_title, user_id)
    now = utcnow()
    docs = [_question_doc(q, category_id, user_id, now) for q in questions]
    inserted = await db.questions.insert_many(docs)
    await db.question_categories.update_one(
        {"_id": category_id}, {"$inc": {"question_count": len(docs)}, "$set": {"updated_at": now}}
    )
    emails = sorted({e.lower() for e in candidate_emails})
    assessment = await assessment_service.create_assessment(
        db,
        workspace_id,
        {
            "name": jd_title.strip()[:200],
            "description": f"AI-generated assessment for {jd_title.strip()}",
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": len(docs),
                    "max_duration_minutes": duration_minutes or settings.PIPELINE_EXAM_MINUTES,
                    "question_ids": [str(i) for i in inserted.inserted_ids],
                }
            ],
            "accessibility": AssessmentAccessibility.MONITORING.value,
            "monitoring_config": dict(MONITORING_CONFIG),
            "expected_candidates": len(emails) or None,
        },
        user_id,
    )
    restriction = {
        "restrict_candidate_access": True,
        "restriction_mode": "INCLUDE",
        "restricted_emails": emails,
    }
    # also lock the assessment's permanent link so nobody outside the invite list can use it
    await db.assessments.update_one({"_id": ObjectId(assessment["id"])}, {"$set": restriction})
    share = await share_service.create_share(
        db,
        assessment["id"],
        workspace_id,
        {"label": f"{jd_title.strip()[:80]} - invited candidates", **restriction},
        user_id,
    )
    logger.info(f"AI pipeline assessment created: assessment_id={assessment['id']} emails={len(emails)}")
    return {
        "category_id": str(category_id),
        "assessment_id": assessment["id"],
        "share_id": share["id"],
        "share_link": share["share_link"],
    }
