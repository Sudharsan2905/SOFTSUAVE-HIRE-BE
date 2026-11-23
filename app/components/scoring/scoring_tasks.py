"""Background scoring tasks called via FastAPI BackgroundTasks (and retried by the reaper).

Scoring state lives on the submission so nothing depends on process memory:

    scoring_status   pending | scored | needs_review | failed
    scoring_started_at   lease start; a run owns the submission for SCORING_LEASE_SECONDS
    rounds_data[].scoring_needs_review   per-round flag feeding the aggregate status

``needs_review`` means at least one essay could not be graded automatically (LLM outage,
invalid replies...) and is flagged for a human; the reaper retries those a few times first.
"""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.utils import utcnow
from app.components.scoring.scoring_service import (
    SCORING_FAILED,
    SCORING_NEEDS_REVIEW,
    SCORING_PENDING,
    SCORING_SCORED,
    score_round,
)
from app.core.logging import logger


async def calculate_and_store_score(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    round_number: int,
) -> None:
    """Score a single round and persist results into rounds_data.

    Updates rounds_data.$[rd].score, percentage, and question_results for the
    given round_number, then recomputes and stores the cumulative submission
    score, percentage and ``scoring_status``.  Never raises: failures set
    ``scoring_status='failed'`` so the reaper retries.
    """
    try:
        oid = ObjectId(submission_id)
        await db.assessment_submissions.update_one(
            {"_id": oid},
            {"$set": {"scoring_status": SCORING_PENDING, "scoring_started_at": utcnow()}},
        )
        sub = await db.assessment_submissions.find_one({"_id": oid})
        if not sub:
            logger.warning("Scoring: submission not found submission_id={}", submission_id)
            return

        target_rd = next(
            (rd for rd in sub.get("rounds_data", []) if rd.get("round_number") == round_number),
            None,
        )
        if not target_rd:
            logger.warning(
                "Scoring: round {} not found submission_id={}", round_number, submission_id
            )
            return

        result = await score_round(db, target_rd)
        needs_review = bool(result.get("needs_manual_review"))

        total_rounds = len(sub.get("rounds_data", []))
        others = [rd for rd in sub.get("rounds_data", []) if rd.get("round_number") != round_number]
        already_scored = [rd for rd in others if rd.get("score") is not None]
        cumulative_pct = round(
            (sum(rd.get("percentage", 0.0) for rd in already_scored) + result["percentage"])
            / total_rounds,
            1,
        )
        cumulative_score = sum(rd.get("score", 0) for rd in already_scored) + result["score"]
        aggregate_review = needs_review or any(rd.get("scoring_needs_review") for rd in others)

        now = utcnow()
        await db.assessment_submissions.update_one(
            {"_id": sub["_id"]},
            {
                "$set": {
                    "rounds_data.$[rd].score": result["score"],
                    "rounds_data.$[rd].percentage": result["percentage"],
                    "rounds_data.$[rd].question_results": result["question_results"],
                    "rounds_data.$[rd].scoring_needs_review": needs_review,
                    "score": cumulative_score,
                    "percentage": cumulative_pct,
                    "scoring_status": SCORING_NEEDS_REVIEW if aggregate_review else SCORING_SCORED,
                    "scoring_completed_at": now,
                    "updated_at": now,
                }
            },
            array_filters=[{"rd.round_number": round_number}],
        )
        logger.info(
            "Round {} scored: submission_id={} pct={:.1f}% status={}",
            round_number,
            submission_id,
            result["percentage"],
            SCORING_NEEDS_REVIEW if aggregate_review else SCORING_SCORED,
        )
    except Exception as exc:
        logger.error(
            "Scoring failed: submission_id={} round={} error={}", submission_id, round_number, exc
        )
        try:
            await db.assessment_submissions.update_one(
                {"_id": ObjectId(submission_id)}, {"$set": {"scoring_status": SCORING_FAILED}}
            )
        except Exception as inner:  # DB itself is down; the reaper will pick it up later
            logger.error("Could not record scoring failure: {}", inner)
