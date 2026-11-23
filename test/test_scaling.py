"""Multi-worker state: leases, stale-session sweep, rescore claims, and the Redis bus.

Redis tests need a real server:  REDIS_TEST_URL=redis://127.0.0.1:6380/0 pytest test/test_scaling.py
"""

import asyncio
import os
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

from app.common.constants.app_constants import SubmissionStatus
from app.common.utils import utcnow
from app.components.candidate import candidate_service
from app.components.scheduler import lease, submission_reaper
from app.components.websocket import connection_manager as cm
from app.components.websocket.redis_bus import RedisBus
from app.core.config import settings

REDIS_URL = os.getenv("REDIS_TEST_URL")
needs_redis = pytest.mark.skipif(not REDIS_URL, reason="REDIS_TEST_URL not set")


class TestLease:
    async def test_single_owner_per_cycle_and_takeover_after_expiry(self, db):
        assert await lease.acquire_lease(db, "job", 30) is True
        assert await lease.acquire_lease(db, "job", 30) is True  # same owner renews
        with patch.object(lease, "OWNER", "someone-else"):
            assert await lease.acquire_lease(db, "job", 30) is False
        await db.scheduler_leases.update_one(
            {"_id": "job"}, {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}}
        )
        with patch.object(lease, "OWNER", "someone-else"):
            assert await lease.acquire_lease(db, "job", 30) is True  # expired -> taken over


def _stale_sub(**over):
    doc = {
        "status": SubmissionStatus.IN_PROGRESS,
        "current_round": 1,
        "last_heartbeat": utcnow() - timedelta(seconds=submission_reaper.hold_stale_seconds() + 30),
        "updated_at": utcnow(),
    }
    doc.update(over)
    return doc


class TestHoldSweep:
    def test_threshold_is_compatible_with_heartbeat_cadence(self):
        assert submission_reaper.hold_stale_seconds() >= 2.5 * settings.WS_PING_SYNC_SECONDS
        assert (
            submission_reaper.hold_stale_seconds()
            >= settings.HOLD_DELAY_SECONDS + settings.HOLD_SWEEP_GRACE_SECONDS
        )

    async def test_abandoned_session_is_held(self, db):
        sub = _stale_sub()
        sub["_id"] = (await db.assessment_submissions.insert_one(sub)).inserted_id
        assert await submission_reaper.sweep_stale_sessions_on_hold(db) == 1
        doc = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["status"] == SubmissionStatus.ON_HOLD and doc["paused_at"] is not None

    async def test_fresh_heartbeat_legacy_and_connected_sessions_are_left_alone(self, db):
        fresh = _stale_sub(last_heartbeat=utcnow())
        legacy = _stale_sub()
        del legacy["last_heartbeat"]  # never recorded a heartbeat (pre-existing sessions)
        connected = _stale_sub()
        ids = []
        for d in (fresh, legacy, connected):
            ids.append((await db.assessment_submissions.insert_one(d)).inserted_id)
        with patch.object(
            cm.manager,
            "is_connected_anywhere",
            AsyncMock(side_effect=lambda sid: sid == str(ids[2])),
        ):
            assert await submission_reaper.sweep_stale_sessions_on_hold(db) == 0
        for oid in ids:
            doc = await db.assessment_submissions.find_one({"_id": oid})
            assert doc["status"] == SubmissionStatus.IN_PROGRESS

    async def test_terminal_sessions_never_held(self, db):
        sub = _stale_sub(status=SubmissionStatus.COMPLETED)
        await db.assessment_submissions.insert_one(sub)
        assert await submission_reaper.sweep_stale_sessions_on_hold(db) == 0


class TestRescore:
    async def _done(self, db, **over):
        doc = {
            "status": SubmissionStatus.COMPLETED,
            "current_round": 1,
            "completed_at": utcnow() - timedelta(minutes=10),
            "rounds_data": [{"round_number": 1, "completed": True}],
        }
        doc.update(over)
        doc["_id"] = (await db.assessment_submissions.insert_one(doc)).inserted_id
        return doc

    async def test_picks_up_needs_review_failed_pending_but_not_scored(self, db):
        a = await self._done(db, scoring_status="needs_review")
        b = await self._done(db, scoring_status="failed")
        c = await self._done(db, scoring_status="pending")
        d = await self._done(db, scoring_status="scored", scoring_completed_at=utcnow())
        e = await self._done(db, scoring_status="needs_review", scoring_attempts=3)  # gave up
        scored: list[str] = []

        async def fake(_db, sid, rnd):
            scored.append(sid)

        with patch.object(submission_reaper.scoring_tasks, "calculate_and_store_score", fake):
            await submission_reaper.rescore_unscored_submissions(db)
        assert set(scored) == {str(a["_id"]), str(b["_id"]), str(c["_id"])}
        assert str(d["_id"]) not in scored and str(e["_id"]) not in scored

    async def test_legacy_unscored_only_within_24h_and_scored_legacy_untouched(self, db):
        fresh_legacy = await self._done(db)  # no status, no scoring_completed_at
        old_legacy = await self._done(db, completed_at=utcnow() - timedelta(days=3))
        scored_legacy = await self._done(db, scoring_completed_at=utcnow())
        scored: list[str] = []

        async def fake(_db, sid, rnd):
            scored.append(sid)

        with patch.object(submission_reaper.scoring_tasks, "calculate_and_store_score", fake):
            await submission_reaper.rescore_unscored_submissions(db)
        assert scored == [str(fresh_legacy["_id"])]
        assert str(old_legacy["_id"]) not in scored and str(scored_legacy["_id"]) not in scored

    async def test_recently_claimed_submission_is_skipped_so_workers_never_double_score(self, db):
        sub = await self._done(db, scoring_status="needs_review", scoring_started_at=utcnow())
        calls = []

        async def fake(_db, sid, rnd):
            calls.append(sid)

        with patch.object(submission_reaper.scoring_tasks, "calculate_and_store_score", fake):
            await submission_reaper.rescore_unscored_submissions(db)
        assert calls == []
        assert (await db.assessment_submissions.find_one({"_id": sub["_id"]}))["scoring_started_at"]

    async def test_only_unscored_or_flagged_rounds_are_rescored(self, db):
        sub = await self._done(
            db,
            scoring_status="needs_review",
            current_round=2,
            rounds_data=[
                {"round_number": 1, "completed": True, "score": 5, "scoring_needs_review": False},
                {"round_number": 2, "completed": True, "scoring_needs_review": True, "score": 1},
            ],
        )
        rounds: list[int] = []

        async def fake(_db, sid, rnd):
            rounds.append(rnd)

        with patch.object(submission_reaper.scoring_tasks, "calculate_and_store_score", fake):
            await submission_reaper.rescore_unscored_submissions(db)
        assert rounds == [2]
        assert sub


async def _wait_for(predicate, timeout: float = 8.0) -> bool:
    """Poll instead of fixed sleeps: pub/sub delivery time varies a lot on a busy host."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self.accept = AsyncMock()

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


class TestInMemoryUnchanged:
    async def test_without_redis_everything_is_local(self):
        mgr = cm.ConnectionManager()
        ws = _FakeWS()
        with patch.object(settings, "REDIS_URL", ""):
            await mgr.connect("s1", ws)
            assert await mgr.send_json("s1", {"type": "x"}) is True
            assert ws.sent == [{"type": "x"}]
            assert await mgr.send_json("nobody", {"type": "x"}) is False
            assert await mgr.is_connected_anywhere("s1") is True
            assert await mgr.is_connected_anywhere("nobody") is False
            await mgr.touch("s1")  # no-op, must not raise


@pytest.fixture
async def two_workers():
    """Two independent managers (= two uvicorn workers) sharing one Redis."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    workers = []
    for _ in range(2):
        bus = RedisBus(REDIS_URL)
        mgr, admin = cm.ConnectionManager(), cm.AdminConnectionManager()
        bus.register(on_instance=mgr._on_bus_message, on_admin=admin._on_bus_message)
        await bus.start()
        workers.append((bus, mgr, admin))
    # wire each manager to its own bus (instead of the process-wide singleton)
    patches = [patch.object(w[1], "_bus", MagicMock(return_value=w[0])) for w in workers] + [
        patch.object(w[2], "_bus", MagicMock(return_value=w[0])) for w in workers
    ]
    for p in patches:
        p.start()
    await asyncio.sleep(0.1)
    yield workers
    for p in patches:
        p.stop()
    for bus, *_ in workers:
        await bus.stop()
    await client.flushdb()
    await client.aclose()


@needs_redis
class TestRedisBus:
    async def test_push_from_worker_b_reaches_socket_on_worker_a(self, two_workers):
        (_, a, _), (_, b, _) = two_workers
        ws = _FakeWS()
        await a.connect("sub1", ws)
        assert b.is_connected("sub1") is False  # not local to B ...
        assert await b.is_connected_anywhere("sub1") is True  # ... but present in Redis
        assert await b.send_json("sub1", {"type": "terminated", "reason": "admin"}) is True
        assert await _wait_for(lambda: len(ws.sent) == 1)
        assert ws.sent == [{"type": "terminated", "reason": "admin"}]

    async def test_push_to_offline_candidate_returns_false(self, two_workers):
        (_, _, _), (_, b, _) = two_workers
        assert await b.send_json("ghost", {"type": "x"}) is False

    async def test_disconnect_clears_presence_and_reconnect_elsewhere_closes_old_socket(
        self, two_workers
    ):
        (_, a, _), (_, b, _) = two_workers
        old, new = _FakeWS(), _FakeWS()
        await a.connect("sub2", old)
        await b.connect("sub2", new)  # candidate reconnected on the other worker
        assert await _wait_for(lambda: old.closed is not None)
        assert old.closed and old.closed[0] == 4009
        # old socket's disconnect on A must not wipe B's presence
        db = MagicMock()
        with patch.object(settings, "HOLD_DELAY_SECONDS", 999):
            await a.disconnect("sub2", db, old)
        assert await b.is_connected_anywhere("sub2") is True
        # message now goes to the new socket via B (local)
        assert await a.send_json("sub2", {"type": "m"}) is True
        assert await _wait_for(lambda: len(new.sent) == 1)
        assert new.sent == [{"type": "m"}]

    async def test_presence_expires_without_pings_and_touch_extends_it(self, two_workers):
        (bus, a, _), (_, b, _) = two_workers
        with patch.object(settings, "WS_PRESENCE_TTL_SECONDS", 2):
            await a.connect("sub3", _FakeWS())
            await asyncio.sleep(1.2)
            await a.touch("sub3")  # ping refreshes
            await asyncio.sleep(1.2)
            assert await b.is_connected_anywhere("sub3") is True
            await asyncio.sleep(2.5)  # no more pings -> worker "died"
            assert await b.is_connected_anywhere("sub3") is False

    async def test_hold_decision_sees_candidate_connected_on_other_worker(self, two_workers):
        (_, a, _), (_, b, _) = two_workers
        await a.connect("sub4", _FakeWS())
        # B's local view says "nobody here" but Redis presence prevents a false ON_HOLD
        assert await b.is_connected_anywhere("sub4") is True

    async def test_admin_broadcast_reaches_admins_on_every_worker_with_access_rules(
        self, two_workers
    ):
        (_, _, admin_a), (_, _, admin_b) = two_workers
        ws_a, ws_b, ws_other = _FakeWS(), _FakeWS(), _FakeWS()
        admin_a.connect("adm-a", ws_a, ["ws1"])
        admin_b.connect("adm-b", ws_b, [], is_super_admin=True)
        admin_b.connect("adm-x", ws_other, ["other-workspace"])
        await admin_a.broadcast_event({"type": "malpractice_event"}, "ws1")
        assert await _wait_for(lambda: len(ws_b.sent) == 1)
        await asyncio.sleep(0.2)  # a duplicate (echo) would have arrived by now
        assert len(ws_a.sent) == 1  # local delivery, exactly once (no echo)
        assert len(ws_b.sent) == 1  # super admin on the OTHER worker
        assert ws_other.sent == []  # no access to ws1

    async def test_bus_listener_survives_garbage_messages(self, two_workers):
        (bus, a, _), _ = two_workers
        ws = _FakeWS()
        await a.connect("sub5", ws)
        await bus.client.publish(bus._instance_channel(bus.instance_id), "not-json")
        await bus.client.publish(bus._instance_channel(bus.instance_id), '"a string"')
        await asyncio.sleep(0.2)
        assert await a.send_json("sub5", {"type": "ok"}) is True


@needs_redis
async def test_redis_outage_degrades_to_local_behaviour():
    """A dead Redis must never break connect/send for sockets on this worker."""
    bus = RedisBus("redis://127.0.0.1:1/0")  # nothing listens here
    mgr = cm.ConnectionManager()
    ws = _FakeWS()
    with patch.object(mgr, "_bus", MagicMock(return_value=bus)):
        await mgr.connect("s", ws)
        assert await mgr.send_json("s", {"type": "x"}) is True
        assert await mgr.send_json("elsewhere", {"type": "x"}) is False
        await mgr.touch("s")
    await bus.stop()
    assert candidate_service and ObjectId  # silence unused imports


@needs_redis
class TestAdminOnlyWorker:
    async def test_admin_connected_to_a_worker_with_no_candidates_still_gets_events(self):
        """Regression: the admin handler must be registered when an admin connects, even if
        no candidate socket ever touched this worker's bus."""
        import redis.asyncio as aioredis

        client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await client.flushdb()
        bus_a, bus_b = RedisBus(REDIS_URL), RedisBus(REDIS_URL)
        admin_a, admin_b = cm.AdminConnectionManager(), cm.AdminConnectionManager()
        with (
            patch.object(admin_a, "_bus", MagicMock(return_value=bus_a)),
            patch.object(admin_b, "_bus", MagicMock(return_value=bus_b)),
        ):
            # production wiring: _bus() registers the handler; emulate that registration
            bus_a.register(on_admin=admin_a._on_bus_message)
            bus_b.register(on_admin=admin_b._on_bus_message)
            ws = _FakeWS()
            admin_a.connect("adm", ws, [], is_super_admin=True)
            await admin_a.ensure_bus()
            await admin_b.ensure_bus()
            await asyncio.sleep(0.1)
            await admin_b.broadcast_event({"type": "malpractice_event"}, "ws9")
            assert await _wait_for(lambda: len(ws.sent) == 1)
        assert len(ws.sent) == 1
        await bus_a.stop()
        await bus_b.stop()
        await client.flushdb()
        await client.aclose()


class TestStaleAnswerContextAcrossWorkers:
    async def test_answer_after_round_advanced_on_another_worker_is_accepted(self, db):
        candidate = ObjectId()
        sub = {
            "candidate_id": candidate,
            "status": SubmissionStatus.IN_PROGRESS,
            "current_round": 1,
            "round_timer_started": True,
            "current_round_seconds": 600,
            "current_deadline_at": utcnow() + timedelta(minutes=5),
            "rounds_data": [
                {"round_number": 1, "max_duration_minutes": 10, "questions": [{"id": "q1"}]},
                {"round_number": 2, "max_duration_minutes": 10, "questions": [{"id": "q2"}]},
            ],
        }
        sid = (await db.assessment_submissions.insert_one(sub)).inserted_id
        sid_s, cid_s = str(sid), str(candidate)
        # worker A caches the round-1 context
        await candidate_service.submit_answer(db, sid_s, cid_s, "q1", "a1")
        # worker B advances the round (A's in-process cache is NOT invalidated)
        await db.assessment_submissions.update_one(
            {"_id": sid},
            {"$set": {"current_round": 2, "current_deadline_at": utcnow() + timedelta(minutes=9)}},
        )
        # the candidate's next request lands on worker A with a stale cache
        res = await candidate_service.submit_answer(db, sid_s, cid_s, "q2", "a2")
        assert res.saved is True
        doc = await db.assessment_submissions.find_one({"_id": sid})
        assert doc["rounds_data"][1]["answers"]["q2"] == "a2"
