"""Background scoring tasks called via FastAPI BackgroundTasks."""

import asyncio

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.utils import utcnow
from app.components.scoring.scoring_service import score_round
from app.core.logging import logger

_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 5


async def notify_final(db: AsyncIOMotorDatabase, submission_id: str) -> None:
    """Fire the AI-pipeline report hook once a submission is fully evaluated (never raises)."""
    try:
        from app.components.ai_pipeline.report_service import send_report_if_ready

        await send_report_if_ready(db, submission_id)
    except Exception as exc:
        logger.error(f"Report hook failed: submission_id={submission_id} error={type(exc).__name__}: {exc}")


async def calculate_and_store_score(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    round_number: int,
    _attempt: int = 1,
) -> None:
    """Score a single round and persist results into rounds_data.

    Updates rounds_data.$[rd].score, percentage, and question_results for the
    given round_number, then recomputes and stores the cumulative submission
    score and percentage.
    """
    try:
        sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
        if not sub:
            logger.warning(f"Scoring: submission not found submission_id={submission_id}")
            return

        target_rd = next(
            (rd for rd in sub.get("rounds_data", []) if rd.get("round_number") == round_number),
            None,
        )
        if not target_rd:
            logger.warning(f"Scoring: round {round_number} not found submission_id={submission_id}")
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
            1,
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
            f"Round {round_number} scored: submission_id={submission_id} "
            f"pct={result['percentage']:.1f}%"
        )
        unresolved = sum(1 for r in result["question_results"].values() if r.get("is_correct") is None)
        if unresolved and _attempt < _MAX_ATTEMPTS:
            logger.warning(
                f"Scoring: {unresolved} answer(s) unevaluated for {submission_id}; "
                f"retry {_attempt}/{_MAX_ATTEMPTS - 1}"
            )
            await asyncio.sleep(_RETRY_DELAY_SECONDS * _attempt)
            await calculate_and_store_score(db, submission_id, round_number, _attempt + 1)
            return
        if unresolved:
            logger.error(f"Scoring: {unresolved} answer(s) still unevaluated for {submission_id}; no report sent")
            return
        await notify_final(db, submission_id)
    except Exception as exc:
        logger.error(
            f"Scoring failed: submission_id={submission_id} round={round_number} "
            f"error={type(exc).__name__}: {exc}"
        )
