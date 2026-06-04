"""Background scoring tasks called via FastAPI BackgroundTasks."""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.utils import utcnow
from app.components.scoring.scoring_service import calculate_submission_score
from app.core.logging import logger


async def calculate_and_store_score(db: AsyncIOMotorDatabase, submission_id: str) -> None:
    """Calculate and persist scores for a completed submission."""
    try:
        sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
        if not sub:
            logger.warning("Scoring: submission not found submission_id=%s", submission_id)
            return

        result = await calculate_submission_score(db, sub)

        await db.assessment_submissions.update_one(
            {"_id": sub["_id"]},
            {
                "$set": {
                    "score": result["score"],
                    "percentage": result["percentage"],
                    "per_round_scores": result["per_round_scores"],
                    "scoring_completed_at": utcnow(),
                    "updated_at": utcnow(),
                }
            },
        )
        logger.info(
            "Scoring complete: submission_id=%s pct=%.1f%%", submission_id, result["percentage"]
        )
    except Exception as exc:
        logger.error("Scoring failed: submission_id=%s error=%s", submission_id, exc)
