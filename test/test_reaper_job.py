"""The reaper as a scheduled one-shot job: lease, exit codes, structured log, and an
end-to-end run of the real `python -m ...submission_reaper` against a real MongoDB.

Real-Mongo tests need MONGO_TEST_URL, e.g.  MONGO_TEST_URL=mongodb://127.0.0.1:27020
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.common.constants.app_constants import SubmissionStatus
from app.common.utils import utcnow
from app.components.scheduler import lease, submission_reaper
from app.core.config import settings

MONGO_URL = os.getenv("MONGO_TEST_URL")
needs_mongo = pytest.mark.skipif(not MONGO_URL, reason="MONGO_TEST_URL not set")
BE_ROOT = Path(__file__).resolve().parent.parent


class TestLeaseGuardsTheCycle:
    async def test_concurrent_cycles_never_run_together(self, db):
        """Two runs started at once: exactly one executes the cycle, the other skips."""
        running = peak = 0
        executed: list[int] = []

        async def slow_cycle(_db):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            executed.append(1)
            await asyncio.sleep(0.2)
            running -= 1
            return {"counts": {"auto_submitted": 0}, "errors": {}}

        with patch.object(submission_reaper, "run_cycle", slow_cycle):
            # second "server" has a different lease owner id
            async def other_server():
                with patch.object(lease, "OWNER", "server-b"):
                    return await submission_reaper.run_once(db)

            results = await asyncio.gather(submission_reaper.run_once(db), other_server())
        statuses = sorted(r["status"] for r in results)
        assert statuses == ["ok", "skipped"]
        assert peak == 1 and len(executed) == 1
        skipped = next(r for r in results if r["status"] == "skipped")
        assert skipped["reason"] == "lease_held"

    async def test_lease_is_released_so_the_next_run_proceeds(self, db):
        first = await submission_reaper.run_once(db)
        second = await submission_reaper.run_once(db)
        assert first["status"] == second["status"] == "ok"
        assert await db.scheduler_leases.count_documents({"_id": "reaper"}) == 0

    async def test_lease_released_even_when_the_cycle_crashes(self, db):
        with patch.object(submission_reaper, "run_cycle", AsyncMock(side_effect=RuntimeError("x"))):
            with pytest.raises(RuntimeError):
                await submission_reaper.run_once(db)
        assert await db.scheduler_leases.count_documents({"_id": "reaper"}) == 0

    async def test_crashed_holder_lease_expires_and_is_taken_over(self, db):
        await db.scheduler_leases.insert_one(
            {"_id": "reaper", "owner": "dead-server", "expires_at": utcnow() - timedelta(seconds=1)}
        )
        assert (await submission_reaper.run_once(db))["status"] == "ok"


class TestStepFailureIsolation:
    async def test_failing_step_is_reported_and_other_steps_still_run(self, db):
        ran: list[str] = []

        async def ok_sweep(_db):
            ran.append("held")
            return 3

        async def ok_rescore(_db):
            ran.append("rescored")
            return 2

        with (
            patch.object(
                submission_reaper,
                "reap_expired_submissions",
                AsyncMock(side_effect=ValueError("boom")),
            ),
            patch.object(submission_reaper, "sweep_stale_sessions_on_hold", ok_sweep),
            patch.object(submission_reaper, "rescore_unscored_submissions", ok_rescore),
        ):
            summary = await submission_reaper.run_once(db)
        assert summary["status"] == "failed"
        assert "ValueError: boom" in summary["errors"]["auto_submitted"]
        assert summary["counts"] == {"auto_submitted": 0, "held": 3, "rescored": 2}
        assert ran == ["held", "rescored"]

    async def test_all_steps_ok_summary(self, db):
        summary = await submission_reaper.run_once(db)
        assert summary["status"] == "ok" and summary["errors"] == {}
        assert set(summary["counts"]) == {"auto_submitted", "held", "rescored"}
        assert "duration_ms" in summary


class TestExitCodes:
    def _patched_main(self, db, **patches):
        from mongomock_motor import AsyncMongoMockClient

        client = AsyncMongoMockClient()
        client.close = lambda: None  # type: ignore[method-assign]
        return patch.object(submission_reaper, "AsyncIOMotorClient", lambda *a, **k: client)

    async def test_exit_0_when_ok(self, db):
        with self._patched_main(db):
            assert await submission_reaper._main() == submission_reaper.EXIT_OK == 0

    async def test_exit_1_when_a_step_fails(self, db):
        with (
            self._patched_main(db),
            patch.object(
                submission_reaper,
                "rescore_unscored_submissions",
                AsyncMock(side_effect=OSError("x")),
            ),
        ):
            code = await submission_reaper._main()
        assert code == submission_reaper.EXIT_FAILED != 0

    async def test_exit_0_when_skipped_because_lease_held(self, db):
        with (
            self._patched_main(db),
            patch.object(
                submission_reaper,
                "run_once",
                AsyncMock(return_value={"status": "skipped", "counts": {}, "errors": {}}),
            ),
        ):
            assert await submission_reaper._main() == 0

    async def test_exit_2_when_infrastructure_is_down(self, db):
        with (
            self._patched_main(db),
            patch.object(
                submission_reaper, "run_once", AsyncMock(side_effect=ConnectionError("mongo down"))
            ),
        ):
            assert await submission_reaper._main() == submission_reaper.EXIT_INFRA == 2

    def test_main_returns_the_exit_code(self):
        with patch.object(submission_reaper, "_main", AsyncMock(return_value=1)):
            assert submission_reaper.main() == 1

    def test_production_defaults(self):
        from app.core.config import Settings

        f = Settings.model_fields
        assert f["REAPER_ENABLED"].default is True
        assert f["REAPER_CRON_MINUTES"].default == 10
        assert f["REAPER_GRACE_SECONDS"].default <= 60  # small: runs are 10 min apart


def _run_job(db_name: str, mongo_url: str = "", extra_env: dict | None = None):
    env = {
        **os.environ,
        "MONGODB_URL": mongo_url or MONGO_URL or "",
        "DATABASE_NAME": db_name,
        "REDIS_URL": "",
        "LOG_LEVEL": "INFO",
        "MONGO_SERVER_SELECTION_TIMEOUT_MS": "800",
        "PYTHONPATH": str(BE_ROOT),
        **(extra_env or {}),
    }
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.components.scheduler.submission_reaper"],
        cwd=BE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _summary(proc: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if "reaper_run " in ln]
    assert len(lines) == 1, f"expected exactly one structured line, got {lines}"
    return json.loads(lines[0].split("reaper_run ", 1)[1].strip())


@pytest.fixture
async def real_db():
    client = AsyncIOMotorClient(MONGO_URL)
    name = f"talentia_reaper_{ObjectId()}"
    yield client[name], name
    await client.drop_database(name)
    client.close()


@needs_mongo
class TestOneShotEndToEnd:
    async def test_real_process_auto_submits_an_overdue_session(self, real_db):
        db, name = real_db
        overdue = utcnow() - timedelta(seconds=settings.REAPER_GRACE_SECONDS + 600)
        sub = {
            "assessment_id": ObjectId(),
            "candidate_id": ObjectId(),
            "status": SubmissionStatus.IN_PROGRESS,
            "current_round": 1,
            "round_timer_started": True,
            "current_round_seconds": 600,
            "current_deadline_at": overdue,
            "rounds_data": [
                {"round_number": 1, "questions": [], "answers": {}, "completed": False}
            ],
            "created_at": utcnow(),
        }
        sid = (await db.assessment_submissions.insert_one(sub)).inserted_id
        fresh = {
            **sub,
            "candidate_id": ObjectId(),
            "current_deadline_at": utcnow() + timedelta(minutes=5),
        }
        fresh.pop("_id", None)
        fid = (await db.assessment_submissions.insert_one(fresh)).inserted_id

        proc = await asyncio.to_thread(_run_job, name)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        summary = _summary(proc)
        assert summary["status"] == "ok"
        assert summary["counts"]["auto_submitted"] == 1
        done = await db.assessment_submissions.find_one({"_id": sid})
        assert done["status"] == SubmissionStatus.COMPLETED
        assert done["auto_submitted"] is True and done["auto_submit_reason"] == "time_expired"
        assert done["rounds_data"][0]["completed"] is True
        assert done["scoring_status"] in ("scored", "needs_review")  # scored in the same run
        live = await db.assessment_submissions.find_one({"_id": fid})
        assert live["status"] == SubmissionStatus.IN_PROGRESS

        # idempotent: a second run finds nothing to do and is still a clean exit 0
        again = await asyncio.to_thread(_run_job, name)
        assert again.returncode == 0 and _summary(again)["counts"]["auto_submitted"] == 0

    async def test_real_process_skips_cleanly_while_the_lease_is_held(self, real_db):
        db, name = real_db
        await db.scheduler_leases.insert_one(
            {
                "_id": "reaper",
                "owner": "another-server",
                "expires_at": utcnow() + timedelta(minutes=2),
            }
        )
        proc = await asyncio.to_thread(_run_job, name)
        assert proc.returncode == 0
        summary = _summary(proc)
        assert summary["status"] == "skipped" and summary["reason"] == "lease_held"

    async def test_real_process_exits_2_when_mongo_is_unreachable(self):
        proc = await asyncio.to_thread(_run_job, "x", "mongodb://127.0.0.1:1")
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert _summary(proc)["status"] == "error"

    async def test_real_process_sweeps_abandoned_session_on_hold(self, real_db):
        db, name = real_db
        stale = utcnow() - timedelta(seconds=submission_reaper.hold_stale_seconds() + 60)
        sub = {
            "status": SubmissionStatus.IN_PROGRESS,
            "current_round": 1,
            "last_heartbeat": stale,
            "current_deadline_at": utcnow() + timedelta(minutes=30),
            "round_timer_started": True,
            "rounds_data": [{"round_number": 1, "questions": [], "answers": {}}],
        }
        sid = (await db.assessment_submissions.insert_one(sub)).inserted_id
        proc = await asyncio.to_thread(_run_job, name)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _summary(proc)["counts"]["held"] == 1
        doc = await db.assessment_submissions.find_one({"_id": sid})
        assert doc["status"] == SubmissionStatus.ON_HOLD


class TestBackgroundScheduler:
    @pytest.fixture
    def cfg(self, monkeypatch):
        monkeypatch.setattr(settings, "REAPER_RUN_ON_STARTUP", False)
        monkeypatch.setattr(settings, "REAPER_CRON_MINUTES", 10)

    async def test_job_registered_with_safe_options(self, cfg):
        from app.components.scheduler import background

        scheduler = background.start(asyncio.get_running_loop())
        try:
            job = scheduler.get_job(background.JOB_ID)
            assert job is not None
            assert job.max_instances == 1 and job.coalesce is True
            assert job.misfire_grace_time == settings.REAPER_MISFIRE_GRACE_SECONDS
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["minute"] == "*/10"
            assert scheduler.get_job(background.STARTUP_JOB_ID) is None  # disabled in this test
        finally:
            background.stop(scheduler)
        assert not scheduler.running

    async def test_restart_replaces_the_job_instead_of_duplicating(self, cfg):
        from app.components.scheduler import background

        first = background.start(asyncio.get_running_loop())
        background.stop(first)
        second = background.start(asyncio.get_running_loop())
        try:
            assert len(second.get_jobs()) == 1
        finally:
            background.stop(second)

    async def test_cycle_runs_off_the_main_thread_and_never_blocks_the_event_loop(
        self, monkeypatch
    ):
        from app.components.scheduler import background

        monkeypatch.setattr(settings, "REAPER_RUN_ON_STARTUP", True)
        monkeypatch.setattr(settings, "REAPER_STARTUP_DELAY_SECONDS", 0)
        seen: dict = {}
        done = threading.Event()

        async def blocking_cycle():
            seen["thread"] = threading.current_thread()
            time.sleep(0.8)  # deliberately BLOCKS (sync sleep) — must only block its own thread
            done.set()
            return {"status": "ok", "counts": {}, "errors": {}}

        monkeypatch.setattr(background, "run_with_own_client", blocking_cycle)
        scheduler = background.start(asyncio.get_running_loop())
        worst_gap = 0.0
        try:
            last = time.perf_counter()
            deadline = last + 8
            while not done.is_set() and time.perf_counter() < deadline:
                await asyncio.sleep(0.02)  # the API's event loop doing "other work"
                now = time.perf_counter()
                worst_gap = max(worst_gap, now - last - 0.02)
                last = now
            assert done.is_set(), "job never ran"
        finally:
            background.stop(scheduler)
        assert seen["thread"] is not threading.main_thread()
        assert seen["thread"].name != threading.current_thread().name
        # the job blocked for 0.8s; the main loop must have stayed responsive meanwhile
        assert worst_gap < 0.3, f"event loop stalled for {worst_gap:.2f}s"

    async def test_job_never_raises_into_the_scheduler(self, monkeypatch):
        from app.components.scheduler import background

        monkeypatch.setattr(
            background, "run_with_own_client", AsyncMock(side_effect=ConnectionError("down"))
        )
        background.reaper_job()  # must swallow + log, not raise


@needs_mongo
class TestSchedulerEndToEnd:
    async def test_scheduled_cycle_auto_submits_overdue_session_and_pushes_on_main_loop(
        self, real_db, monkeypatch
    ):
        from app.components.scheduler import background
        from app.components.websocket import connection_manager as cm

        db, name = real_db
        monkeypatch.setattr(settings, "MONGODB_URL", MONGO_URL)
        monkeypatch.setattr(settings, "DATABASE_NAME", name)
        monkeypatch.setattr(settings, "REDIS_URL", "")
        monkeypatch.setattr(settings, "REAPER_RUN_ON_STARTUP", True)
        monkeypatch.setattr(settings, "REAPER_STARTUP_DELAY_SECONDS", 0)
        overdue = utcnow() - timedelta(seconds=settings.REAPER_GRACE_SECONDS + 600)
        sid = (
            await db.assessment_submissions.insert_one(
                {
                    "assessment_id": ObjectId(),
                    "candidate_id": ObjectId(),
                    "status": SubmissionStatus.IN_PROGRESS,
                    "current_round": 1,
                    "round_timer_started": True,
                    "current_round_seconds": 600,
                    "current_deadline_at": overdue,
                    "rounds_data": [
                        {"round_number": 1, "questions": [], "answers": {}, "completed": False}
                    ],
                    "created_at": utcnow(),
                }
            )
        ).inserted_id

        pushed_on: list[threading.Thread] = []

        class _WS:
            sent: list = []
            accept = AsyncMock()

            async def send_json(self, data):
                pushed_on.append(threading.current_thread())
                self.sent.append(data)

        ws = _WS()
        await cm.manager.connect(str(sid), ws)  # socket lives on THIS (main) loop
        scheduler = background.start(asyncio.get_running_loop())
        try:
            doc = None
            for _ in range(200):
                doc = await db.assessment_submissions.find_one({"_id": sid})
                if doc["status"] == SubmissionStatus.COMPLETED:
                    break
                await asyncio.sleep(0.1)
        finally:
            background.stop(scheduler)
            cm.manager._connections.pop(str(sid), None)
        assert doc["status"] == SubmissionStatus.COMPLETED
        assert doc["auto_submitted"] is True and doc["auto_submit_reason"] == "time_expired"
        assert doc["scoring_status"] in ("scored", "needs_review")
        await asyncio.sleep(0.2)
        assert ws.sent == [{"type": "time_expired"}]
        # the websocket write happened on the API's thread, not the scheduler's
        assert pushed_on and all(t is threading.main_thread() for t in pushed_on)
