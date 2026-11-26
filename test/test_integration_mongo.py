"""Integration tests against a REAL MongoDB (arrayFilters, atomic operators, concurrency).

Skipped unless MONGO_TEST_URL is set, e.g.
    docker run -d -p 27018:27017 mongo:7.0
    MONGO_TEST_URL=mongodb://127.0.0.1:27018 pytest test/test_integration_mongo.py
"""

import asyncio
import os
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.common.constants.app_constants import SubmissionStatus
from app.common.exceptions import ForbiddenException
from app.common.utils import utcnow
from app.components.candidate import candidate_service, timing
from app.components.scheduler import submission_reaper
from app.core.config import settings

URL = os.getenv("MONGO_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MONGO_TEST_URL not set")


@pytest.fixture
async def rdb():
    client = AsyncIOMotorClient(URL, tz_aware=False)
    name = f"talentia_it_{ObjectId()}"
    yield client[name]
    await client.drop_database(name)
    client.close()


async def _sub(rdb, **over):
    now = utcnow()
    candidate_id = ObjectId()
    doc = {
        "assessment_id": ObjectId(),
        "candidate_id": candidate_id,
        "status": SubmissionStatus.IN_PROGRESS,
        "current_round": 1,
        "round_timer_started": True,
        "current_round_seconds": 300,
        "current_deadline_at": now + timedelta(minutes=5),
        "workspace_id": ObjectId(),
        "rounds_data": [
            {
                "round_number": n,
                "max_duration_minutes": 5,
                "questions": [{"id": f"q{n}"}],
                "answers": {},
                "completed": False,
                "started_at": now if n == 1 else None,
            }
            for n in (1, 2, 3)
        ],
        "malpractice_count": 0,
        "malpractice_events": [],
        "monitoring_details": {"tab_monitoring": True},
        "created_at": now,
        "updated_at": now,
    }
    doc.update(over)
    doc["_id"] = (await rdb.assessment_submissions.insert_one(doc)).inserted_id
    return doc


class TestFinishRoundReal:
    async def test_advances_round_and_starts_provisional_timer(self, rdb):
        sub = await _sub(rdb)
        res = await candidate_service.finish_round(rdb, str(sub["_id"]), str(sub["candidate_id"]))
        assert res == {"completed": False, "next_round": 2, "finished_round": 1}
        doc = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["current_round"] == 2
        assert doc["rounds_data"][0]["completed"] is True
        assert doc["rounds_data"][1]["started_at"] is not None
        assert doc["round_timer_started"] is False  # next round's timer begins on start_interview
        assert timing.remaining_seconds(doc) == 300

    async def test_concurrent_finishes_advance_exactly_once(self, rdb):
        sub = await _sub(rdb)
        cid = str(sub["candidate_id"])

        async def attempt():
            try:
                return await candidate_service.finish_round(rdb, str(sub["_id"]), cid, 1)
            except ForbiddenException:
                return "rejected"

        results = await asyncio.gather(*(attempt() for _ in range(10)))
        doc = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["current_round"] == 2  # never double-advanced to 3
        advanced = [r for r in results if isinstance(r, dict) and not r.get("already_completed")]
        assert len(advanced) == 1  # exactly one real advance; the rest are no-ops

    async def test_last_round_completes_and_replay_is_idempotent(self, rdb):
        sub = await _sub(rdb, current_round=3)
        cid = str(sub["candidate_id"])
        first = await candidate_service.finish_round(rdb, str(sub["_id"]), cid)
        again = await candidate_service.finish_round(rdb, str(sub["_id"]), cid)
        assert first == {"completed": True, "finished_round": 3}
        assert again["already_completed"] is True
        doc = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["status"] == SubmissionStatus.COMPLETED

    async def test_malpractice_session_cannot_be_revived(self, rdb):
        sub = await _sub(rdb, status=SubmissionStatus.MALPRACTICE)
        with pytest.raises(ForbiddenException):
            await candidate_service.finish_round(rdb, str(sub["_id"]), str(sub["candidate_id"]))
        doc = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["status"] == SubmissionStatus.MALPRACTICE


class TestStartInterviewReal:
    async def test_timer_starts_once_even_when_called_repeatedly(self, rdb):
        sub = await _sub(
            rdb, round_timer_started=False, current_deadline_at=utcnow() + timedelta(minutes=20)
        )
        cid = str(sub["candidate_id"])
        await candidate_service.start_interview(rdb, str(sub["_id"]), cid)
        first = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        await asyncio.sleep(0.05)
        await asyncio.gather(
            *(candidate_service.start_interview(rdb, str(sub["_id"]), cid) for _ in range(5))
        )
        second = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        assert first["round_timer_started"] is True
        assert second["current_deadline_at"] == first["current_deadline_at"]
        assert second["rounds_data"][0]["deadline_at"] == first["rounds_data"][0]["deadline_at"]


class TestMalpracticeConcurrency:
    async def test_parallel_violations_never_undercount(self, rdb):
        sub = await _sub(rdb)
        cid = str(sub["candidate_id"])
        results = await asyncio.gather(
            *(
                candidate_service.flag_malpractice(rdb, str(sub["_id"]), cid, "tab_switch")
                for _ in range(3)
            )
        )
        doc = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["malpractice_count"] == 3
        assert len(doc["malpractice_events"]) == 3
        assert sorted(r.event_index for r in results if r.event_index is not None) == [0, 1, 2]
        assert doc["status"] == SubmissionStatus.MALPRACTICE


class TestEvidenceReal:
    async def test_write_once_slot(self, rdb):
        sub = await _sub(
            rdb,
            malpractice_events=[{"type": "tab_switch", "round": 1, "timestamp": utcnow()}],
        )
        cid = str(sub["candidate_id"])
        upload = AsyncMock()
        with patch.object(candidate_service.s3_service, "upload", upload):
            await asyncio.gather(
                *(
                    candidate_service.upload_malpractice_media(
                        rdb, str(sub["_id"]), 0, cid, video_bytes=b"x"
                    )
                    for _ in range(5)
                )
            )
        assert upload.call_count == 1  # only the first writer claims the slot


class TestReaperReal:
    async def test_end_to_end_auto_submit_and_idempotent_rerun(self, rdb):
        old = await _sub(
            rdb,
            current_deadline_at=utcnow() - timedelta(seconds=settings.REAPER_GRACE_SECONDS + 30),
        )
        live = await _sub(rdb)
        scored = []

        async def fake_score(_db, sid, rnd):
            scored.append((sid, rnd))

        with patch.object(submission_reaper.scoring_tasks, "calculate_and_store_score", fake_score):
            n1 = await submission_reaper.reap_expired_submissions(rdb)
            n2 = await submission_reaper.reap_expired_submissions(rdb)
        assert (n1, n2) == (1, 0)
        assert scored == [(str(old["_id"]), 1)]
        done = await rdb.assessment_submissions.find_one({"_id": old["_id"]})
        assert done["status"] == SubmissionStatus.COMPLETED and done["auto_submitted"] is True
        assert done["rounds_data"][0]["completed"] is True
        assert (await rdb.assessment_submissions.find_one({"_id": live["_id"]}))[
            "status"
        ] == SubmissionStatus.IN_PROGRESS

    async def test_two_runners_never_double_submit(self, rdb):
        for _ in range(20):
            await _sub(
                rdb,
                current_deadline_at=utcnow()
                - timedelta(seconds=settings.REAPER_GRACE_SECONDS + 30),
            )
        scored = []

        async def fake_score(_db, sid, rnd):
            scored.append(sid)

        with patch.object(submission_reaper.scoring_tasks, "calculate_and_store_score", fake_score):
            a, b = await asyncio.gather(
                submission_reaper.reap_expired_submissions(rdb),
                submission_reaper.reap_expired_submissions(rdb),
            )
        assert a + b == 20
        assert len(scored) == len(set(scored)) == 20

    async def test_legacy_pending_records_are_backfilled_then_reaped(self, rdb):
        started = utcnow() - timedelta(hours=6)
        sub = await _sub(rdb)
        await rdb.assessment_submissions.update_one(
            {"_id": sub["_id"]},
            {
                "$unset": {
                    "current_deadline_at": "",
                    "current_round_seconds": "",
                    "round_timer_started": "",
                },
                "$set": {"rounds_data.0.started_at": started},
            },
        )
        with patch.object(
            submission_reaper.scoring_tasks, "calculate_and_store_score", AsyncMock()
        ):
            assert await submission_reaper.reap_expired_submissions(rdb) == 1
        doc = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["status"] == SubmissionStatus.COMPLETED


class TestOverallPercentage:
    """Overall % = total correct / total questions, not the average of round percentages."""

    @staticmethod
    def _question():
        return {
            "_id": ObjectId(),
            "question_type": "mcq_single",
            "options": [
                {"id": "a", "text": "A", "is_correct": True},
                {"id": "b", "text": "B", "is_correct": False},
            ],
        }

    @staticmethod
    def _round(number, questions, answers):
        return {
            "round_number": number,
            "questions": [
                {"id": str(q["_id"]), "type": "mcq_single", "text": "?"} for q in questions
            ],
            "answers": {str(q["_id"]): a for q, a in zip(questions, answers, strict=True)},
            "completed": True,
        }

    async def test_not_an_average_of_rounds(self, rdb):
        from app.components.assessment.result import evaluate_result
        from app.components.scoring import scoring_tasks

        r1 = [self._question()]  # 1 of 1 correct -> round 100%
        r2 = [self._question() for _ in range(3)]  # 1 of 3 correct -> round 33.3%
        await rdb.questions.insert_many(r1 + r2)
        sub = await _sub(
            rdb,
            status=SubmissionStatus.COMPLETED,
            rounds_data=[self._round(1, r1, ["a"]), self._round(2, r2, ["a", "b", "b"])],
        )

        await scoring_tasks.calculate_and_store_score(rdb, str(sub["_id"]), 1)
        await scoring_tasks.calculate_and_store_score(rdb, str(sub["_id"]), 2)

        stored = await rdb.assessment_submissions.find_one({"_id": sub["_id"]})
        # 2 correct of 4 questions = 50.0; averaging the rounds would have given 66.7
        assert stored["score"] == 2
        assert stored["percentage"] == 50.0
        assert stored["scoring_status"] == "scored"
        assert evaluate_result(stored, 50) == "passed"
        assert evaluate_result(stored, 51) == "failed"


class TestPassFailStats:
    """result_query uses $not/$elemMatch, so check it against a real MongoDB."""

    async def test_counts_only_fully_scored_completed_submissions(self, rdb):
        from app.components.assessment import assessment_service

        aid = ObjectId()
        await rdb.assessments.insert_one({"_id": aid, "pass_percentage": 50})

        def sub(pct, rounds, **over):
            return {
                "assessment_id": aid,
                "candidate_id": ObjectId(),
                "status": SubmissionStatus.COMPLETED,
                "scoring_status": "scored",
                "percentage": pct,
                "rounds_data": rounds,
                "is_active": True,
                **over,
            }

        done = [{"round_number": 1, "score": 1}, {"round_number": 2, "score": 2}]
        await rdb.assessment_submissions.insert_many(
            [
                sub(80.0, done),  # passed
                sub(50.0, done),  # passed (boundary)
                sub(49.9, done),  # failed
                sub(10.0, [{"round_number": 1, "score": 1}, {"round_number": 2}]),  # stale
                sub(90.0, [{"round_number": 1, "score": None}]),  # unscored round
                sub(90.0, done, status=SubmissionStatus.MALPRACTICE),  # not completed
                sub(90.0, done, scoring_status="needs_review"),  # not fully scored
                sub(70.0, []),  # no rounds recorded: nothing left to score
            ]
        )

        stats = await assessment_service.get_submission_stats(rdb, str(aid))
        assert (stats["pass_percentage"], stats["passed"], stats["failed"]) == (50, 3, 1)

    async def test_list_filter_on_real_mongo(self, rdb):
        from app.components.assessment import assessment_service

        aid = ObjectId()
        await rdb.assessments.insert_one({"_id": aid, "pass_percentage": 60})
        done = [{"round_number": 1, "score": 3}]

        def sub(pct, **over):
            return {
                "assessment_id": aid,
                "candidate_id": ObjectId(),
                "status": SubmissionStatus.COMPLETED,
                "scoring_status": "scored",
                "percentage": pct,
                "rounds_data": done,
                "is_active": True,
                "started_at": utcnow(),
                **over,
            }

        await rdb.assessment_submissions.insert_many(
            [
                sub(90.0),
                sub(60.0),
                sub(20.0),
                sub(59.9),
                sub(95.0, status=SubmissionStatus.MALPRACTICE),
                sub(70.0, rounds_data=[{"round_number": 1}]),  # last round still unscored
            ]
        )

        async def listed(**kw):
            out = await assessment_service.get_submissions(
                rdb, str(aid), None, "started_at", "desc", 1, 20, **kw
            )
            return sorted(s["percentage"] for s in out["submissions"]), out["pagination"]["total"]

        assert await listed(result="passed") == ([60.0, 90.0], 2)
        assert await listed(result="failed") == ([20.0, 59.9], 2)
        assert (await listed())[1] == 6
        assert await listed(status="malpractice", result="passed") == ([], 0)
