"""The reaper's production scheduler: an APScheduler cron job on a BACKGROUND THREAD.

* ``BackgroundScheduler`` owns its own thread; the job body runs ``asyncio.run`` there, i.e.
  on a separate event loop with its own Mongo client — the API's main thread / event loop is
  never blocked by a cycle.
* Cron trigger ``*/REAPER_CRON_MINUTES`` (default every 10 min); ``max_instances=1``,
  ``coalesce=True`` (a backlog of missed ticks collapses into one run), fixed job id,
  ``replace_existing``, ``misfire_grace_time``.
* Every uvicorn worker / instance starts the scheduler, but each cycle is guarded by a Mongo
  lease (``run_once``), so exactly one executes it.
* One extra run shortly after startup (``REAPER_RUN_ON_STARTUP``) clears any backlog left by
  downtime without waiting for the next cron tick.

An abandoned session is therefore auto-submitted within about
``REAPER_CRON_MINUTES`` + ``REAPER_GRACE_SECONDS`` after its deadline.
"""

import asyncio
import json
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from motor.motor_asyncio import AsyncIOMotorClient

from app.common.utils import utcnow
from app.components.scheduler.runtime import set_main_loop
from app.components.scheduler.submission_reaper import run_once
from app.core.config import settings
from app.core.logging import logger

JOB_ID = "submission_reaper"
STARTUP_JOB_ID = "submission_reaper_startup"


async def run_with_own_client() -> dict:
    """One leased cycle on the CURRENT loop with a private Mongo client (never the API's)."""
    client: AsyncIOMotorClient = AsyncIOMotorClient(
        settings.MONGODB_URL,
        maxPoolSize=10,
        serverSelectionTimeoutMS=settings.MONGO_SERVER_SELECTION_TIMEOUT_MS,
    )
    try:
        return await run_once(client[settings.DATABASE_NAME])
    finally:
        client.close()


def reaper_job() -> None:
    """APScheduler entry point — runs on the scheduler's worker thread. Never raises."""
    try:
        summary = asyncio.run(run_with_own_client())
    except Exception as exc:  # DB down etc.: log and wait for the next tick
        summary = {
            "status": "error",
            "counts": {},
            "errors": {"infra": f"{type(exc).__name__}: {exc}"},
        }
    logger.info(f"reaper_run {json.dumps(summary, sort_keys=True)}")


def start(loop: asyncio.AbstractEventLoop | None = None) -> BackgroundScheduler:
    """Create and start the scheduler (call from the FastAPI lifespan)."""
    set_main_loop(loop)
    minutes = max(1, min(59, settings.REAPER_CRON_MINUTES))
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        reaper_job,
        CronTrigger(minute=f"*/{minutes}", timezone="UTC"),
        id=JOB_ID,
        name="Auto-submit reaper",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=settings.REAPER_MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    if settings.REAPER_RUN_ON_STARTUP:
        scheduler.add_job(
            reaper_job,
            DateTrigger(
                run_date=utcnow() + timedelta(seconds=settings.REAPER_STARTUP_DELAY_SECONDS)
            ),
            id=STARTUP_JOB_ID,
            max_instances=1,
            misfire_grace_time=settings.REAPER_MISFIRE_GRACE_SECONDS,
            replace_existing=True,
        )
    scheduler.start()
    logger.info(
        f"Reaper scheduler started: cron=*/{minutes} min, grace={settings.REAPER_GRACE_SECONDS}s"
    )
    return scheduler


def stop(scheduler: BackgroundScheduler | None) -> None:
    """Shut down without waiting for a running cycle (it is a daemon thread; the lease
    expires on its own)."""
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
    set_main_loop(None)
