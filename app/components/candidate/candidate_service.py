import base64
import random
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import QuestionType, SubmissionStatus
from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.utils import (
    build_pagination_meta,
    paginate_query,
    safe_regex,
    serialize_doc,
    serialize_docs,
    utcnow,
)
from app.core.logging import logger

_ERR_ASSESSMENT_NOT_FOUND = "Assessment not found"
_ERR_ACTIVE_SUBMISSION_NOT_FOUND = "Active submission not found"


async def get_candidate_assessment(db: AsyncIOMotorDatabase, share_link: str) -> dict:
    """Return assessment metadata for a candidate via share link, without internal question IDs.

    Raises:
        NotFoundException: If the share link is invalid.
    """
    doc = await db.assessments.find_one({"share_link": share_link})
    if not doc:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)
    safe = serialize_doc(doc)
    for r in safe.get("rounds", []):
        r.pop("question_ids", None)
    return safe


async def start_assessment(db: AsyncIOMotorDatabase, share_link: str, candidate_id: str) -> dict:
    """Start or resume a candidate's assessment submission.

    - Returns existing submission if already IN_PROGRESS.
    - Transitions PENDING → IN_PROGRESS without re-sampling questions.
    - Creates a new submission (randomly sampling questions per round) on first call.

    Raises:
        NotFoundException: If the assessment share link is invalid.
        ForbiddenException: If the submission is already COMPLETED or MALPRACTICE.
    """
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

    rounds_data = []
    for round_cfg in assessment.get("rounds", []):
        question_ids = round_cfg.get("question_ids", [])
        required = round_cfg["question_count"]

        selected_ids = (
            random.sample(question_ids, min(required, len(question_ids)))
            if len(question_ids) >= required
            else question_ids
        )

        questions_raw = await db.questions.find({"_id": {"$in": selected_ids}}).to_list(
            len(selected_ids)
        )

        safe_questions = []
        for q in questions_raw:
            sq = serialize_doc(q)
            q_type = sq.pop("question_type", "essay")
            if q_type in [QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI]:
                opts = [
                    {k: v for k, v in o.items() if k != "is_correct"} for o in sq.get("options", [])
                ]
                random.shuffle(opts)
                sq["options"] = opts
            sq.pop("correct_answer", None)
            sq["text"] = sq.pop("question_text", "")
            sq["type"] = "mcq_multiple" if q_type == QuestionType.MCQ_MULTI else q_type
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
        {
            "_id": ObjectId(submission_id),
            "candidate_id": ObjectId(candidate_id),
            "status": SubmissionStatus.IN_PROGRESS,
        }
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
    else:
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


async def _calculate_score(db: AsyncIOMotorDatabase, submission: dict) -> tuple[int, float]:
    """Compute (correct_count, percentage) across all MCQ rounds in a submission.

    Essay questions are skipped.
    """
    total = 0
    correct = 0

    for rd in submission.get("rounds_data", []):
        questions = rd.get("questions", [])
        answers = rd.get("answers", {})

        for q in questions:
            qid = q.get("id")
            total += 1

            if q.get("question_type") == QuestionType.ESSAY:
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
    db: AsyncIOMotorDatabase, submission_id: str, candidate_id: str, file_bytes: bytes
) -> None:
    """Append a base64-encoded screenshot to the submission's screenshots array.

    Silently no-ops if the submission is not found.
    """
    sub = await db.assessment_submissions.find_one(
        {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
    )
    if not sub:
        return

    screenshot_data = base64.b64encode(file_bytes).decode()
    round_number = sub.get("current_round", 1)
    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
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
    db: AsyncIOMotorDatabase, submission_id: str, candidate_id: str, malpractice_type: str
) -> None:
    """Mark a submission as MALPRACTICE if tab monitoring is enabled; silently returns otherwise.

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

    assessment = await db.assessments.find_one({"_id": sub["assessment_id"]})
    if assessment:
        monitoring_config = assessment.get("monitoring_config") or {}
        if not monitoring_config.get("tab_monitoring", True):
            return

    await db.assessment_submissions.update_one(
        {"_id": sub["_id"]},
        {
            "$set": {
                "status": SubmissionStatus.MALPRACTICE,
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
        {"$match": {"status": "in_progress"}},
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
        pipeline.append({"$match": {"assessment.accessibility": monitoring_type}})
    if search:
        pipeline.append(
            {
                "$match": {
                    "$or": [
                        {"candidate.first_name": {"$regex": safe_regex(search), "$options": "i"}},
                        {"candidate.last_name": {"$regex": safe_regex(search), "$options": "i"}},
                        {"assessment.name": {"$regex": safe_regex(search), "$options": "i"}},
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
