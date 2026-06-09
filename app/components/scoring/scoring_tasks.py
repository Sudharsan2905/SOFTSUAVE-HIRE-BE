"""Background scoring tasks called via FastAPI BackgroundTasks."""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.utils import utcnow
from app.components.scoring.scoring_service import score_round
from app.core.logging import logger


async def calculate_and_store_score(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    round_number: int,
) -> None:
    """Score a single round and persist results into rounds_data.

    Updates rounds_data.$[rd].score, percentage, and question_results for the
    given round_number, then recomputes and stores the cumulative submission
    score and percentage.
    """
    try:
        sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
        if not sub:
            logger.warning("Scoring: submission not found submission_id=%s", submission_id)
            return

        target_rd = next(
            (rd for rd in sub.get("rounds_data", []) if rd.get("round_number") == round_number),
            None,
        )
        if not target_rd:
            logger.warning(
                "Scoring: round %d not found submission_id=%s", round_number, submission_id
            )
            return

        result = await score_round(db, target_rd)

        total_rounds = len(sub.get("rounds_data", []))
        already_scored = [
            rd
            for rd in sub.get("rounds_data", [])
            if rd.get("round_number") != round_number and rd.get("score") is not None
        ]
        cumulative_pct = round(
            (sum(rd.get("percentage", 0.0) for rd in already_scored) + result["percentage"])
            / total_rounds,
            2,
        )
        cumulative_score = sum(rd.get("score", 0) for rd in already_scored) + result["score"]

        now = utcnow()
        await db.assessment_submissions.update_one(
            {"_id": sub["_id"]},
            {
                "$set": {
                    "rounds_data.$[rd].score": result["score"],
                    "rounds_data.$[rd].percentage": result["percentage"],
                    "rounds_data.$[rd].question_results": result["question_results"],
                    "score": cumulative_score,
                    "percentage": cumulative_pct,
                    "scoring_completed_at": now,
                    "updated_at": now,
                }
            },
            array_filters=[{"rd.round_number": round_number}],
        )
        logger.info(
            "Round %d scored: submission_id=%s pct=%.1f%%",
            round_number,
            submission_id,
            result["percentage"],
        )
    except Exception as exc:
        logger.error(
            "Scoring failed: submission_id=%s round=%d error=%s", submission_id, round_number, exc
        )
