"""Send each candidate ONE email: username + temporary password + exam link."""

import asyncio

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.services import email_service
from app.common.utils import utcnow
from app.components.ai_pipeline.constants import PIPELINE_CANDIDATES, PIPELINES
from app.components.ai_pipeline.email_templates import build_invite_html, exam_url
from app.components.ai_pipeline.provisioning import provision_candidate
from app.core.config import settings
from app.core.logging import logger


async def _mark(db: AsyncIOMotorDatabase, cand_id: ObjectId, **fields: object) -> None:
    await db[PIPELINE_CANDIDATES].update_one(
        {"_id": cand_id}, {"$set": {**fields, "updated_at": utcnow()}}
    )


async def provision_one(db: AsyncIOMotorDatabase, cand: dict) -> str | None:
    """Create/reuse the candidate's user; returns the temp password or None when it failed."""
    try:
        user_id, password = await provision_candidate(
            db, email=cand["email"], name=cand.get("name"), phone=cand.get("phone")
        )
    except Exception as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        await _mark(
            db, cand["_id"], status="failed", failed_step="provisioning", error=str(detail)[:400],
            invite_status="failed", invite_error=f"Provisioning failed: {detail}"[:400],
        )
        return None
    await _mark(db, cand["_id"], user_id=user_id, status="provisioned", error=None, failed_step=None)
    return password


async def invite_one(
    db: AsyncIOMotorDatabase, pipeline: dict, cand: dict, password: str | None = None
) -> bool:
    """Send the single invite email; records success or the detailed failure. True when sent."""
    if password is None:
        password = await provision_one(db, cand)
        if password is None:
            return False
    body = build_invite_html(
        cand.get("name") or "", pipeline["jd_title"], exam_url(pipeline["share_link"]), cand["email"], password
    )
    ok, error = await email_service.send_html(
        cand["email"], f"Assessment Invitation: {pipeline['jd_title']}", body
    )
    if ok:
        await _mark(
            db, cand["_id"], invite_status="sent", invite_error=None, invited_at=utcnow(),
            status="invited", error=None, failed_step=None,
        )
    else:
        await _mark(
            db, cand["_id"], invite_status="failed", invite_error=error[:500],
            failed_step="invites", error=error[:400],
        )
        logger.error(f"Invite failed for {cand['email']}: {error}")
    return ok


async def send_invites(
    db: AsyncIOMotorDatabase,
    pipeline_id: ObjectId,
    *,
    candidate_ids: list[ObjectId] | None = None,
    force: bool = False,
    passwords: dict[str, str] | None = None,
    on_progress: object = None,
) -> dict:
    """Invite candidates with an email that have not been invited yet (or all selected if forced)."""
    pipeline = await db[PIPELINES].find_one({"_id": pipeline_id})
    if not pipeline or not pipeline.get("share_link"):
        return {"invited": 0, "failed": 0}
    query: dict = {"pipeline_id": pipeline_id, "email": {"$ne": None}}
    if not force:
        query["invite_status"] = {"$ne": "sent"}
    if candidate_ids is not None:
        query["_id"] = {"$in": candidate_ids}
    cands = await db[PIPELINE_CANDIDATES].find(query).to_list(None)
    sem = asyncio.Semaphore(max(1, settings.PIPELINE_CONCURRENCY))
    passwords = passwords or {}

    async def _one(c: dict) -> bool:
        async with sem:
            ok = await invite_one(db, pipeline, c, passwords.get(str(c["_id"])))
            if callable(on_progress):
                await on_progress(ok)
            return ok

    outcomes = await asyncio.gather(*(_one(c) for c in cands))
    return {"invited": outcomes.count(True), "failed": outcomes.count(False)}
