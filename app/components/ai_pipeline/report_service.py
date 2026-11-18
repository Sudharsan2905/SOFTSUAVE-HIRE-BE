"""Send ONE result report per candidate once the exam is submitted and every answer is evaluated."""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import SubmissionStatus
from app.common.services import email_service
from app.common.utils import utcnow
from app.components.ai_pipeline.constants import PIPELINE_CANDIDATES, PIPELINES
from app.components.ai_pipeline.email_templates import build_report_html
from app.core.logging import logger

_TERMINAL = {
    SubmissionStatus.COMPLETED.value,
    SubmissionStatus.TERMINATED.value,
    SubmissionStatus.MALPRACTICE.value,
}


def unevaluated_count(sub: dict) -> int:
    """Questions with no definite correct/incorrect result (e.g. essay scoring failed)."""
    count = 0
    for rd in sub.get("rounds_data", []):
        results = rd.get("question_results") or {}
        for q in rd.get("questions", []):
            if (results.get(q.get("id")) or {}).get("is_correct") is None:
                count += 1
    return count


def is_fully_evaluated(sub: dict) -> bool:
    status = getattr(sub.get("status"), "value", sub.get("status"))
    total = sum(len(rd.get("questions", [])) for rd in sub.get("rounds_data", []))
    return status in _TERMINAL and total > 0 and unevaluated_count(sub) == 0


async def _find_pipeline_candidate(db: AsyncIOMotorDatabase, sub: dict) -> tuple[dict, dict] | None:
    pipelines = await db[PIPELINES].find({"assessment_id": sub.get("assessment_id")}).to_list(None)
    for pipeline in pipelines:
        cand = await db[PIPELINE_CANDIDATES].find_one(
            {"pipeline_id": pipeline["_id"], "user_id": sub.get("candidate_id")}
        )
        if cand:
            return pipeline, cand
    return None


async def _admin_email(db: AsyncIOMotorDatabase, pipeline: dict) -> str | None:
    admin = await db.users.find_one({"_id": pipeline.get("created_by")}, {"email": 1})
    return (admin or {}).get("email")


async def _deliver(db: AsyncIOMotorDatabase, pipeline: dict, cand: dict, sub: dict) -> tuple[bool, str]:
    admin_email = await _admin_email(db, pipeline)
    html = build_report_html(pipeline["jd_title"], cand.get("name") or "", sub)
    return await email_service.send_html(
        cand["email"],
        f"Your assessment report: {pipeline['jd_title']}",
        html,
        cc=[admin_email] if admin_email else None,
    )


async def send_report_if_ready(
    db: AsyncIOMotorDatabase, submission_id: str, *, force: bool = False
) -> bool:
    """Send the single report email when the submission is fully evaluated. True when sent now.

    Guarded by ``report_sent_at`` (atomically claimed) so a report is never sent twice.
    Failures are stored on the pipeline candidate (``report_error``).
    """
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub or not is_fully_evaluated(sub):
        return False
    found = await _find_pipeline_candidate(db, sub)
    if not found:
        return False
    pipeline, cand = found
    if not cand.get("email"):
        return False
    col = db[PIPELINE_CANDIDATES]
    claim_filter: dict = {"_id": cand["_id"]}
    if not force:
        claim_filter["report_sent_at"] = None
    claimed = await col.find_one_and_update(
        claim_filter,
        {"$set": {"report_sent_at": utcnow(), "report_error": None, "submission_id": sub["_id"]}},
    )
    if not claimed:
        logger.info(f"Report already sent for submission_id={submission_id}; skipping")
        return False
    ok, error = await _deliver(db, pipeline, cand, sub)
    if not ok:
        await col.update_one(
            {"_id": cand["_id"]},
            {"$set": {"report_sent_at": None, "report_error": error, "updated_at": utcnow()}},
        )
        logger.error(f"Report email failed for {cand['email']}: {error}")
        return False
    logger.info(f"Report email sent to {cand['email']} (submission_id={submission_id})")
    return True


async def resend_report(db: AsyncIOMotorDatabase, cand: dict) -> bool:
    """Admin-triggered re-send for a candidate whose submission is fully evaluated."""
    pipeline = await db[PIPELINES].find_one({"_id": cand["pipeline_id"]}) or {}
    sub = await db.assessment_submissions.find_one(
        {"candidate_id": cand.get("user_id"), "assessment_id": pipeline.get("assessment_id")}
    )
    if not sub or not is_fully_evaluated(sub):
        return False
    return await send_report_if_ready(db, str(sub["_id"]), force=True)
