"""Auto-submit reaper.

Finds IN_PROGRESS submissions whose server-side round deadline has passed (plus a grace
period — the live browser normally auto-submits itself first), finalises them as
COMPLETED with ``auto_submitted=True`` and scores them.

Safe to run on several instances at once: every submission is claimed with an atomic
``update_one`` filtered on ``status == IN_PROGRESS``, so only one runner wins.

Run modes:
    * PRODUCTION: APScheduler cron job (default every 10 min) on a background thread inside
      the API — see ``background.py``.  Every worker has the scheduler; a Mongo lease means
      only one executes each cycle.
    * MANUAL / BACKFILL: ``python -m app.components.scheduler.submission_reaper`` runs ONE
      cycle and exits (0 ok/skipped, 1 a step failed, 2 infra error).
    Either way one structured ``reaper_run {...}`` log line is written per run.
"""

import asyncio
import contextlib
import json
import sys
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.common.constants.app_constants import SubmissionStatus
from app.common.utils import utcnow
from app.components.candidate import candidate_service, timing
from app.components.scheduler.lease import acquire_lease, release_lease
from app.components.scheduler.runtime import on_main_loop
from app.components.scoring import scoring_tasks
from app.components.websocket.connection_manager import manager
from app.components.websocket.redis_bus import shutdown_bus
from app.core.config import settings
from app.core.logging import logger, setup_logging

_SUMMARY_PROJECTION = {
    "status": 1,
    "current_round": 1,
    "current_deadline_at": 1,
    "current_round_seconds": 1,
    "round_timer_started": 1,
    "paused_at": 1,
    "rounds_data.round_number": 1,
    "rounds_data.max_duration_minutes": 1,
    "rounds_data.started_at": 1,
}


async def backfill_deadlines(db: AsyncIOMotorDatabase, limit: int = 1000) -> int:
    """Give legacy IN_PROGRESS submissions (created before server-side timing) a deadline.

    Derived from the current round's ``started_at`` + duration.  Records that can't be
    derived are left alone.
    """
    cursor = db.assessment_submissions.find(
        {"status": SubmissionStatus.IN_PROGRESS, "current_deadline_at": {"$exists": False}},
        _SUMMARY_PROJECTION,
    ).limit(limit)
    updated = 0
    async for sub in cursor:
        deadline = timing.effective_deadline(sub)
        if deadline is None:
            continue
        result = await db.assessment_submissions.update_one(
            {"_id": sub["_id"], "current_deadline_at": {"$exists": False}},
            {
                "$set": {
                    "current_deadline_at": deadline,
                    "current_round_seconds": timing.round_seconds(timing.current_round_doc(sub)),
                }
            },
        )
        updated += result.modified_count
    return updated


async def _auto_submit(db: AsyncIOMotorDatabase, sub: dict, now: datetime) -> int | None:
    """Atomically finalise one overdue submission. Returns the round to score, or None."""
    current = sub.get("current_round", 1)
    result = await db.assessment_submissions.update_one(
        {"_id": sub["_id"], "status": SubmissionStatus.IN_PROGRESS, "current_round": current},
        {
            "$set": {
                "status": SubmissionStatus.COMPLETED,
                "completed_at": now,
                "updated_at": now,
                "auto_submitted": True,
                "auto_submit_reason": "time_expired",
                "rounds_data.$[rd].completed": True,
                "rounds_data.$[rd].completed_at": now,
            }
        },
        array_filters=[{"rd.round_number": current}],
    )
    candidate_service.invalidate_answer_ctx(str(sub["_id"]))
    if result.modified_count == 0:
        return None  # someone else (the candidate, an admin, another runner) got there first
    # The socket (if any) lives on the API's main loop; this may run on the reaper thread.
    try:
        await on_main_loop(manager.send_json(str(sub["_id"]), {"type": "time_expired"}))
    except Exception as exc:  # a missed push is covered by the browser timer / next sync
        logger.warning("time_expired push failed: {}", exc)
    return int(current)


async def reap_expired_submissions(db: AsyncIOMotorDatabase) -> int:
    """Auto-submit and score every overdue session once. Returns how many were submitted."""
    await backfill_deadlines(db)

    now = utcnow()
    cutoff = now - timedelta(seconds=settings.REAPER_GRACE_SECONDS)
    overdue = (
        await db.assessment_submissions.find(
            {"status": SubmissionStatus.IN_PROGRESS, "current_deadline_at": {"$lt": cutoff}},
            _SUMMARY_PROJECTION,
        )
        .limit(settings.REAPER_BATCH_SIZE)
        .to_list(settings.REAPER_BATCH_SIZE)
    )
    if not overdue:
        return 0

    sem = asyncio.Semaphore(settings.REAPER_SCORING_CONCURRENCY)

    async def _process(sub: dict) -> bool:
        try:
            round_number = await _auto_submit(db, sub, now)
            if round_number is None:
                return False
            logger.info("Auto-submitted overdue session: submission_id={}", sub["_id"])
            async with sem:
                await scoring_tasks.calculate_and_store_score(db, str(sub["_id"]), round_number)
            return True
        except Exception as exc:
            logger.error("Reaper failed for submission_id={}: {}", sub.get("_id"), exc)
            return False

    results = await asyncio.gather(*(_process(sub) for sub in overdue))
    return sum(1 for r in results if r)


async def rescore_unscored_submissions(db: AsyncIOMotorDatabase) -> int:
    """Retry scoring that never finished or needed manual review.

    Picks finished sessions whose ``scoring_status`` is pending / failed / needs_review
    (process restart, OpenAI outage...), plus legacy sessions that never recorded a status
    and have no ``scoring_completed_at`` (last 24h only, so history is never mass-rescored).
    Each run first claims the submission with a lease so concurrent workers never double
    score, and gives up after ``SCORING_MAX_ATTEMPTS`` (the flag stays for an admin).
    """
    now = utcnow()
    grace = now - timedelta(minutes=2)
    legacy_oldest = now - timedelta(hours=24)
    lease_expired = now - timedelta(seconds=settings.SCORING_LEASE_SECONDS)
    pending = (
        await db.assessment_submissions.find(
            {
                "status": {
                    "$in": [
                        SubmissionStatus.COMPLETED,
                        SubmissionStatus.MALPRACTICE,
                        SubmissionStatus.TERMINATED,
                    ]
                },
                "completed_at": {"$lt": grace},
                "scoring_attempts": {"$not": {"$gte": settings.SCORING_MAX_ATTEMPTS}},
                "$and": [
                    {
                        "$or": [
                            {"scoring_status": {"$in": ["pending", "failed", "needs_review"]}},
                            {
                                "scoring_status": {"$exists": False},
                                "scoring_completed_at": None,
                                "completed_at": {"$gte": legacy_oldest},
                            },
                        ]
                    },
                    {
                        "$or": [
                            {"scoring_started_at": None},
                            {"scoring_started_at": {"$lt": lease_expired}},
                        ]
                    },
                ],
            },
            {
                "current_round": 1,
                "rounds_data.round_number": 1,
                "rounds_data.completed": 1,
                "rounds_data.score": 1,
                "rounds_data.scoring_needs_review": 1,
            },
        )
        .limit(50)
        .to_list(50)
    )
    sem = asyncio.Semaphore(settings.REAPER_SCORING_CONCURRENCY)

    async def _score(sub: dict) -> None:
        current = int(sub.get("current_round", 1))
        rounds = [
            int(rd["round_number"])
            for rd in sub.get("rounds_data", [])
            if (rd.get("completed") or rd.get("round_number") == current)
            and (rd.get("score") is None or rd.get("scoring_needs_review"))
        ]
        # Claim: only the worker that wins this update scores the submission.
        claim = await db.assessment_submissions.update_one(
            {
                "_id": sub["_id"],
                "$or": [
                    {"scoring_started_at": None},
                    {"scoring_started_at": {"$lt": lease_expired}},
                ],
            },
            {"$inc": {"scoring_attempts": 1}, "$set": {"scoring_started_at": utcnow()}},
        )
        if not claim.modified_count:
            return
        async with sem:
            for number in rounds or [current]:
                await scoring_tasks.calculate_and_store_score(db, str(sub["_id"]), number)

    await asyncio.gather(*(_score(s) for s in pending), return_exceptions=True)
    return len(pending)


def hold_stale_seconds() -> int:
    """How old ``last_heartbeat`` must be before a session counts as abandoned.

    At least HOLD_DELAY + grace, and never below 2.5x the heartbeat persistence interval
    (the WebSocket handler only writes ``last_heartbeat`` every WS_PING_SYNC_SECONDS).
    """
    return max(
        settings.HOLD_DELAY_SECONDS + settings.HOLD_SWEEP_GRACE_SECONDS,
        int(2.5 * settings.WS_PING_SYNC_SECONDS),
    )


async def sweep_stale_sessions_on_hold(db: AsyncIOMotorDatabase) -> int:
    """Put IN_PROGRESS sessions whose socket vanished ON_HOLD.

    This replaces dependence on in-memory hold timers: a worker restart/crash loses its
    timers, but the stale ``last_heartbeat`` still gives the session away.  A session is only
    held when no live socket exists anywhere (Redis presence, or the local manager).
    Sessions that never recorded a heartbeat are left alone (legacy / not yet connected).
    """
    cutoff = utcnow() - timedelta(seconds=hold_stale_seconds())
    stale = (
        await db.assessment_submissions.find(
            {"status": SubmissionStatus.IN_PROGRESS, "last_heartbeat": {"$lt": cutoff}},
            {"_id": 1},
        )
        .limit(settings.REAPER_BATCH_SIZE)
        .to_list(settings.REAPER_BATCH_SIZE)
    )
    held = 0
    for sub in stale:
        sid = str(sub["_id"])
        if await on_main_loop(manager.is_connected_anywhere(sid)):
            continue
        await candidate_service.put_session_on_hold(db, sid)
        held += 1
    return held


REAPER_LEASE_NAME = "reaper"
EXIT_OK = 0
EXIT_FAILED = 1  # at least one step raised (the others still ran)
EXIT_INFRA = 2  # could not even connect / acquire the lease


async def run_cycle(db: AsyncIOMotorDatabase) -> dict:
    """One reaper cycle, no lease: auto-submit overdue -> ON_HOLD sweep -> rescore retry.

    Every step runs even if an earlier one fails; failures are collected, not raised, so
    one bad step never starves the others.  Returns ``{"counts": {...}, "errors": {...}}``.
    """
    steps = (
        ("auto_submitted", reap_expired_submissions),
        ("held", sweep_stale_sessions_on_hold),
        ("rescored", rescore_unscored_submissions),
    )
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    for name, step in steps:
        try:
            counts[name] = await step(db)
        except Exception as exc:
            counts[name] = 0
            errors[name] = f"{type(exc).__name__}: {exc}"
            logger.error("Reaper step {} failed: {}", name, exc)
    return {"counts": counts, "errors": errors}


async def run_once(db: AsyncIOMotorDatabase) -> dict:
    """Run ONE full cycle under the Mongo lease; the summary dict is the run's record.

    ``status`` is ``ok`` | ``failed`` | ``skipped`` (lease held by another run, which is a
    clean outcome — overlapping timers / several servers never run the cycle concurrently).
    """
    started = utcnow()
    if not await acquire_lease(db, REAPER_LEASE_NAME, settings.REAPER_LEASE_SECONDS):
        return {"status": "skipped", "reason": "lease_held", "counts": {}, "errors": {}}
    try:
        result = await run_cycle(db)
    finally:
        await release_lease(db, REAPER_LEASE_NAME)
    result["status"] = "failed" if result["errors"] else "ok"
    result["duration_ms"] = int((utcnow() - started).total_seconds() * 1000)
    return result


async def _main() -> int:
    """Entry point body: connect, run ONE cycle, log ONE structured line, return exit code."""
    setup_logging(settings.LOG_LEVEL)
    client: AsyncIOMotorClient = AsyncIOMotorClient(
        settings.MONGODB_URL, serverSelectionTimeoutMS=settings.MONGO_SERVER_SELECTION_TIMEOUT_MS
    )
    try:
        summary = await run_once(client[settings.DATABASE_NAME])
    except Exception as exc:  # DB unreachable etc.
        summary = {
            "status": "error",
            "counts": {},
            "errors": {"infra": f"{type(exc).__name__}: {exc}"},
        }
    finally:
        client.close()
        with contextlib.suppress(Exception):
            await shutdown_bus()
    logger.info(f"reaper_run {json.dumps(summary, sort_keys=True)}")
    if summary["status"] == "error":
        return EXIT_INFRA
    return EXIT_FAILED if summary["status"] == "failed" else EXIT_OK


def main() -> int:
    """``python -m app.components.scheduler.submission_reaper`` — exit 0 ok/skipped,
    1 a step failed, 2 infrastructure error."""
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
