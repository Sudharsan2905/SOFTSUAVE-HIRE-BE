from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from typing import Optional
import random
from app.common.exceptions import NotFoundException, ForbiddenException
from app.common.utils import (
    utcnow,
    serialize_doc,
    serialize_docs,
    paginate_query,
    build_pagination_meta,
)
from app.common.constants.app_constants import SubmissionStatus


async def get_candidate_assessment(db: AsyncIOMotorDatabase, share_link: str) -> dict:
    doc = await db.assessments.find_one({"share_link": share_link})
    if not doc:
        raise NotFoundException("Assessment not found")
    safe = serialize_doc(doc)
    for r in safe.get("rounds", []):
        r.pop("question_ids", None)
    return safe


async def start_assessment(
    db: AsyncIOMotorDatabase, share_link: str, candidate_id: str
) -> dict:
    assessment = await db.assessments.find_one({"share_link": share_link})
    if not assessment:
        raise NotFoundException("Assessment not found")

    existing = await db.assessment_submissions.find_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
        }
    )

    if existing:
        status = existing.get("status")
        if status in [SubmissionStatus.COMPLETED, SubmissionStatus.MALPRACTICE]:
            raise ForbiddenException(
                "You have already completed this assessment. Please contact admin for re-access."
            )
        if status == SubmissionStatus.IN_PROGRESS:
            return serialize_doc(existing)
        if status == SubmissionStatus.PENDING:
            await db.assessment_submissions.update_one(
                {"_id": existing["_id"]},
                {"$set": {"status": SubmissionStatus.IN_PROGRESS, "started_at": utcnow(), "updated_at": utcnow()}},
            )
            updated = await db.assessment_submissions.find_one({"_id": existing["_id"]})
            return serialize_doc(updated)

    rounds_data = []
    for round_cfg in assessment.get("rounds", []):
        question_ids = round_cfg.get("question_ids", [])
        required = round_cfg["question_count"]

        selected_ids = (
            random.sample(question_ids, min(required, len(question_ids)))
            if len(question_ids) >= required
            else question_ids
        )

        questions_raw = await db.questions.find(
            {"_id": {"$in": selected_ids}}
        ).to_list(len(selected_ids))

        safe_questions = []
        for q in questions_raw:
            sq = serialize_doc(q)
            if sq.get("question_type") in ["mcq_single", "mcq_multi"]:
                opts = [
                    {k: v for k, v in o.items() if k != "is_correct"}
                    for o in sq.get("options", [])
                ]
                random.shuffle(opts)
                sq["options"] = opts
            sq.pop("correct_answer", None)
            safe_questions.append(sq)

        random.shuffle(safe_questions)

        rounds_data.append(
            {
                "round_number": round_cfg["round_number"],
                "question_count": required,
                "max_duration_minutes": round_cfg["max_duration_minutes"],
                "questions": safe_questions,
                "answers": {},
                "completed": False,
                "started_at": None,
            }
        )

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
    return serialize_doc(submission)


async def submit_answer(
    db: AsyncIOMotorDatabase,
    share_link: str,
    candidate_id: str,
    question_id: str,
    answer,
    round_number: int,
) -> dict:
    assessment = await db.assessments.find_one({"share_link": share_link})
    if not assessment:
        raise NotFoundException("Assessment not found")

    sub = await db.assessment_submissions.find_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
            "status": SubmissionStatus.IN_PROGRESS,
        }
    )
    if not sub:
        raise NotFoundException("Active submission not found")

    idx = round_number - 1
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


async def finish_round(
    db: AsyncIOMotorDatabase, share_link: str, candidate_id: str, round_number: int
) -> dict:
    assessment = await db.assessments.find_one({"share_link": share_link})
    if not assessment:
        raise NotFoundException("Assessment not found")

    sub = await db.assessment_submissions.find_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
            "status": SubmissionStatus.IN_PROGRESS,
        }
    )
    if not sub:
        raise NotFoundException("Active submission not found")

    total_rounds = len(assessment.get("rounds", []))
    idx = round_number - 1

    if round_number >= total_rounds:
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
        return {"completed": True, "percentage": percentage}
    else:
        await db.assessment_submissions.update_one(
            {"_id": sub["_id"]},
            {
                "$set": {
                    "current_round": round_number + 1,
                    "updated_at": utcnow(),
                    f"rounds_data.{idx}.completed": True,
                }
            },
        )
        return {"completed": False, "next_round": round_number + 1}


async def _calculate_score(db: AsyncIOMotorDatabase, submission: dict) -> tuple[int, float]:
    total = 0
    correct = 0

    for rd in submission.get("rounds_data", []):
        questions = rd.get("questions", [])
        answers = rd.get("answers", {})

        for q in questions:
            qid = q.get("id")
            total += 1

            if q.get("question_type") == "essay":
                continue

            original = await db.questions.find_one({"_id": ObjectId(qid)})
            if not original:
                continue

            correct_ids = {o["id"] for o in original.get("options", []) if o.get("is_correct")}
            given = answers.get(qid, [])
            if isinstance(given, str):
                given = [given]

            if set(given) == correct_ids:
                correct += 1

    pct = round((correct / total * 100) if total > 0 else 0.0, 2)
    return correct, pct


async def save_screenshot(
    db: AsyncIOMotorDatabase,
    share_link: str,
    candidate_id: str,
    screenshot_data: str,
    round_number: int,
):
    assessment = await db.assessments.find_one({"share_link": share_link})
    if not assessment:
        return

    await db.assessment_submissions.update_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
            "status": SubmissionStatus.IN_PROGRESS,
        },
        {
            "$push": {
                "screenshots": {
                    "url": screenshot_data,
                    "round": round_number,
                    "taken_at": utcnow(),
                }
            }
        },
    )


async def flag_malpractice(
    db: AsyncIOMotorDatabase,
    share_link: str,
    candidate_id: str,
    reason: str,
    details: Optional[str] = None,
):
    assessment = await db.assessments.find_one({"share_link": share_link})
    if not assessment:
        raise NotFoundException("Assessment not found")

    monitoring_config = assessment.get("monitoring_config") or {}
    if not monitoring_config.get("tab_monitoring", True):
        return

    await db.assessment_submissions.update_one(
        {
            "assessment_id": assessment["_id"],
            "candidate_id": ObjectId(candidate_id),
            "status": SubmissionStatus.IN_PROGRESS,
        },
        {
            "$set": {
                "status": SubmissionStatus.MALPRACTICE,
                "is_malpractice": True,
                "malpractice_reason": reason,
                "malpractice_details": details,
                "completed_at": utcnow(),
                "updated_at": utcnow(),
            }
        },
    )


async def get_live_interviews(
    db: AsyncIOMotorDatabase,
    search: Optional[str],
    monitoring_type: Optional[str],
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
) -> dict:
    skip, limit = paginate_query(page, page_size)
    sort_dir = 1 if sort_order == "asc" else -1

    pipeline = [
        {"$match": {"status": "in_progress"}},
        {"$lookup": {"from": "assessments", "localField": "assessment_id", "foreignField": "_id", "as": "assessment"}},
        {"$unwind": "$assessment"},
        {"$lookup": {"from": "users", "localField": "candidate_id", "foreignField": "_id", "as": "candidate"}},
        {"$unwind": "$candidate"},
        {"$project": {"candidate.password_hash": 0, "rounds_data": 0}},
    ]

    if monitoring_type:
        pipeline.append({"$match": {"assessment.accessibility": monitoring_type}})
    if search:
        pipeline.append(
            {"$match": {"$or": [
                {"candidate.name": {"$regex": search, "$options": "i"}},
                {"assessment.name": {"$regex": search, "$options": "i"}},
            ]}}
        )

    count_res = await db.assessment_submissions.aggregate(
        pipeline + [{"$count": "total"}]
    ).to_list(1)
    total = count_res[0]["total"] if count_res else 0

    pipeline += [{"$sort": {"started_at": sort_dir}}, {"$skip": skip}, {"$limit": limit}]
    docs = await db.assessment_submissions.aggregate(pipeline).to_list(limit)

    return {
        "live_interviews": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }
