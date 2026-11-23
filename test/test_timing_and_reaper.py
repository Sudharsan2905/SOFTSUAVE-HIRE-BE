"""Tests for server-owned round timing, the auto-submit reaper and the exam-integrity guards."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.common.constants.app_constants import SubmissionStatus
from app.common.exceptions import ForbiddenException, ValidationException
from app.common.utils import utcnow
from app.components.candidate import candidate_service, timing
from app.components.scheduler import submission_reaper
from app.core.config import settings


def _sub(**over):
    base = {
        "status": SubmissionStatus.IN_PROGRESS,
        "current_round": 1,
        "rounds_data": [{"round_number": 1, "max_duration_minutes": 10, "started_at": None}],
    }
    base.update(over)
    return base


class TestRemainingSeconds:
    def test_not_started_reports_full_duration(self):
        sub = _sub(round_timer_started=False, current_round_seconds=600)
        assert timing.remaining_seconds(sub) == 600

    def test_counts_down_from_deadline(self):
        now = utcnow()
        sub = _sub(
            round_timer_started=True,
            current_round_seconds=600,
            current_deadline_at=now + timedelta(seconds=90),
        )
        assert 88 <= timing.remaining_seconds(sub, now) <= 90

    def test_never_negative(self):
        now = utcnow()
        sub = _sub(round_timer_started=True, current_deadline_at=now - timedelta(hours=1))
        assert timing.remaining_seconds(sub, now) == 0

    def test_naive_mongo_datetimes_are_handled(self):
        now = utcnow()
        deadline = (now + timedelta(seconds=30)).replace(tzinfo=None)  # Mongo returns naive
        sub = _sub(round_timer_started=True, current_deadline_at=deadline)
        assert 28 <= timing.remaining_seconds(sub, now) <= 30

    def test_on_hold_clock_is_frozen_at_paused_at(self):
        now = utcnow()
        sub = _sub(
            status=SubmissionStatus.ON_HOLD,
            round_timer_started=True,
            current_deadline_at=now + timedelta(seconds=100),
            paused_at=now - timedelta(seconds=40),
        )
        # deadline - paused_at = 140, regardless of how long it has been on hold
        assert 138 <= timing.remaining_seconds(sub, now) <= 140
        assert timing.is_expired(sub, now=now + timedelta(days=1)) is False

    def test_legacy_document_derives_from_round_started_at(self):
        now = utcnow()
        sub = _sub(
            rounds_data=[
                {
                    "round_number": 1,
                    "max_duration_minutes": 10,
                    "started_at": now - timedelta(minutes=4),
                }
            ]
        )
        assert 355 <= timing.remaining_seconds(sub, now) <= 360

    def test_default_duration_only_lives_on_the_server(self):
        assert timing.round_seconds({}) == settings.DEFAULT_ROUND_MINUTES * 60
        assert timing.round_seconds({"max_duration_minutes": 0}) == 30 * 60


@pytest.fixture
def no_array_filters(db):
    """mongomock lacks arrayFilters: drop positional ($[x]) keys, keep the rest real.

    The positional behaviour itself is covered against a real MongoDB in
    test_integration_mongo.py.
    """
    from mongomock.collection import Collection

    real_update = Collection.update_one

    def update(self, flt, upd, upsert=False, array_filters=None, **kw):
        upd = dict(upd)
        if "$set" in upd:
            upd["$set"] = {k: v for k, v in upd["$set"].items() if "$[" not in k}
        return real_update(self, flt, upd, upsert=upsert, **kw)

    with patch.object(Collection, "update_one", update):
        yield


@pytest.fixture
async def assessment(db, workspace, super_admin):
    doc = {
        "workspace_id": workspace["_id"],
        "name": "A",
        "share_link": "tl-1",
        "is_active": True,
        "rounds": [
            {"round_number": 1, "question_count": 1, "max_duration_minutes": 5, "question_ids": []},
            {"round_number": 2, "question_count": 1, "max_duration_minutes": 5, "question_ids": []},
        ],
        "accessibility": "normal",
        "monitoring_config": {},
        "created_by": super_admin["_id"],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    doc["_id"] = (await db.assessments.insert_one(doc)).inserted_id
    return doc


async def _make_submission(db, assessment, candidate_user, **over):
    now = utcnow()
    doc = {
        "assessment_id": assessment["_id"],
        "candidate_id": candidate_user["_id"],
        "status": SubmissionStatus.IN_PROGRESS,
        "current_round": 1,
        "round_timer_started": True,
        "current_round_seconds": 300,
        "current_deadline_at": now + timedelta(minutes=5),
        "rounds_data": [
            {
                "round_number": 1,
                "max_duration_minutes": 5,
                "questions": [{"id": "q1"}],
                "answers": {},
                "completed": False,
                "started_at": now,
            },
            {
                "round_number": 2,
                "max_duration_minutes": 5,
                "questions": [{"id": "q2"}],
                "answers": {},
                "completed": False,
                "started_at": None,
            },
        ],
        "malpractice_count": 0,
        "malpractice_events": [],
        "monitoring_details": {"tab_monitoring": True},
        "screenshots": [],
        "created_at": now,
        "updated_at": now,
    }
    doc.update(over)
    doc["_id"] = (await db.assessment_submissions.insert_one(doc)).inserted_id
    return doc


class TestSubmitAnswerGuards:
    async def test_rejects_answer_after_deadline(self, db, assessment, candidate_user):
        sub = await _make_submission(
            db,
            assessment,
            candidate_user,
            current_deadline_at=utcnow() - timedelta(seconds=settings.SUBMIT_GRACE_SECONDS + 60),
        )
        with pytest.raises(ForbiddenException):
            await candidate_service.submit_answer(
                db, str(sub["_id"]), str(candidate_user["_id"]), "q1", "x"
            )

    async def test_accepts_answer_within_grace(self, db, assessment, candidate_user):
        sub = await _make_submission(
            db, assessment, candidate_user, current_deadline_at=utcnow() - timedelta(seconds=5)
        )
        result = await candidate_service.submit_answer(
            db, str(sub["_id"]), str(candidate_user["_id"]), "q1", "x"
        )
        assert result.saved is True

    async def test_rejects_foreign_question_id_and_dotted_keys(
        self, db, assessment, candidate_user
    ):
        sub = await _make_submission(db, assessment, candidate_user)
        for bad in ("q2", "q1.evil", "$set"):
            with pytest.raises(ValidationException):
                await candidate_service.submit_answer(
                    db, str(sub["_id"]), str(candidate_user["_id"]), bad, "x"
                )

    async def test_rejects_oversized_answer(self, db, assessment, candidate_user):
        sub = await _make_submission(db, assessment, candidate_user)
        with pytest.raises(ValidationException):
            await candidate_service.submit_answer(
                db, str(sub["_id"]), str(candidate_user["_id"]), "q1", "x" * 20_001
            )


class TestStartInterviewIsIdempotent:
    async def test_second_call_does_not_reset_deadline(
        self, db, assessment, candidate_user, no_array_filters
    ):
        sub = await _make_submission(
            db,
            assessment,
            candidate_user,
            round_timer_started=False,
            current_deadline_at=utcnow() + timedelta(minutes=20),
        )
        first_resp = await candidate_service.start_interview(
            db, str(sub["_id"]), str(candidate_user["_id"])
        )
        first = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        second_resp = await candidate_service.start_interview(
            db, str(sub["_id"]), str(candidate_user["_id"])
        )
        second = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        assert first_resp["remaining_seconds"] == 300
        assert second_resp["remaining_seconds"] <= 300
        assert first["round_timer_started"] is True
        assert second["current_deadline_at"] == first["current_deadline_at"]


class TestGetCurrentRoundRemaining:
    async def test_remaining_comes_from_server_not_client_value(
        self, db, assessment, candidate_user
    ):
        sub = await _make_submission(
            db,
            assessment,
            candidate_user,
            remaining_seconds=999_999,  # stale / forged client value must be ignored
            current_deadline_at=utcnow() + timedelta(seconds=100),
        )
        with patch.object(
            candidate_service, "_resolve_questions_images", AsyncMock(return_value=[])
        ):
            data = await candidate_service.get_current_round(
                db, str(sub["_id"]), str(candidate_user["_id"])
            )
        assert 95 <= data["remaining_seconds"] <= 100

    async def test_malpractice_session_is_blocked(self, db, assessment, candidate_user):
        sub = await _make_submission(
            db, assessment, candidate_user, status=SubmissionStatus.MALPRACTICE
        )
        with pytest.raises(ForbiddenException):
            await candidate_service.get_current_round(
                db, str(sub["_id"]), str(candidate_user["_id"])
            )


class TestReaper:
    async def test_auto_submits_overdue_and_leaves_active_ones(
        self, db, assessment, candidate_user, workspace, no_array_filters
    ):
        overdue = await _make_submission(
            db,
            assessment,
            candidate_user,
            current_deadline_at=utcnow() - timedelta(seconds=settings.REAPER_GRACE_SECONDS + 60),
        )
        # a second candidate still inside the grace window
        other = {
            "first_name": "O",
            "last_name": "C",
            "email": "other@example.com",
            "role": "candidate",
            "is_active": True,
        }
        other["_id"] = (await db.users.insert_one(other)).inserted_id
        fresh = await _make_submission(
            db,
            assessment,
            other,
            current_deadline_at=utcnow() - timedelta(seconds=10),
        )
        scored: list = []

        async def fake_score(_db, sid, rnd):
            scored.append((sid, rnd))

        with patch.object(submission_reaper.scoring_tasks, "calculate_and_store_score", fake_score):
            count = await submission_reaper.reap_expired_submissions(db)

        assert count == 1
        assert scored == [(str(overdue["_id"]), 1)]
        done = await db.assessment_submissions.find_one({"_id": overdue["_id"]})
        assert done["status"] == SubmissionStatus.COMPLETED
        assert done["auto_submitted"] is True
        assert done["auto_submit_reason"] == "time_expired"
        still = await db.assessment_submissions.find_one({"_id": fresh["_id"]})
        assert still["status"] == SubmissionStatus.IN_PROGRESS

    async def test_on_hold_sessions_are_never_reaped(self, db, assessment, candidate_user):
        sub = await _make_submission(
            db,
            assessment,
            candidate_user,
            status=SubmissionStatus.ON_HOLD,
            paused_at=utcnow() - timedelta(hours=5),
            current_deadline_at=utcnow() - timedelta(hours=4),
        )
        assert await submission_reaper.reap_expired_submissions(db) == 0
        doc = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["status"] == SubmissionStatus.ON_HOLD

    async def test_backfills_legacy_records_without_deadline(self, db, assessment, candidate_user):
        started = utcnow() - timedelta(hours=3)
        sub = await _make_submission(db, assessment, candidate_user)
        await db.assessment_submissions.update_one(
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
        assert await submission_reaper.backfill_deadlines(db) == 1
        doc = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        expected = started + timedelta(minutes=5)
        got = timing.aware(doc["current_deadline_at"])
        assert abs((got - expected).total_seconds()) < 1

    async def test_does_not_double_submit_when_already_completed(
        self, db, assessment, candidate_user, no_array_filters
    ):
        sub = await _make_submission(
            db, assessment, candidate_user, status=SubmissionStatus.COMPLETED
        )
        result = await submission_reaper._auto_submit(db, sub, utcnow())
        assert result is None


class TestMalpracticeIntegrity:
    async def test_completed_session_cannot_be_flipped_to_malpractice(
        self, db, assessment, candidate_user
    ):
        sub = await _make_submission(
            db, assessment, candidate_user, status=SubmissionStatus.COMPLETED
        )
        res = await candidate_service.flag_malpractice(
            db, str(sub["_id"]), str(candidate_user["_id"]), "tab_switch"
        )
        assert res.is_terminal is False
        doc = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["status"] == SubmissionStatus.COMPLETED
        assert doc["malpractice_events"] == []

    async def test_strikes_accumulate_atomically_to_termination(
        self, db, assessment, candidate_user
    ):
        sub = await _make_submission(db, assessment, candidate_user)
        sid, cid = str(sub["_id"]), str(candidate_user["_id"])
        r1 = await candidate_service.flag_malpractice(db, sid, cid, "tab_switch")
        r2 = await candidate_service.flag_malpractice(db, sid, cid, "tab_switch")
        r3 = await candidate_service.flag_malpractice(db, sid, cid, "tab_switch")
        assert (r1.event_index, r2.event_index, r3.event_index) == (0, 1, 2)
        assert [r1.is_terminal, r2.is_terminal, r3.is_terminal] == [False, False, True]
        doc = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["malpractice_count"] == 3
        assert len(doc["malpractice_events"]) == 3
        assert doc["status"] == SubmissionStatus.MALPRACTICE


class TestEvidenceWriteOnce:
    async def test_existing_clip_cannot_be_replaced(self, db, assessment, candidate_user):
        sub = await _make_submission(
            db,
            assessment,
            candidate_user,
            malpractice_events=[
                {
                    "type": "tab_switch",
                    "round": 1,
                    "timestamp": utcnow(),
                    "screen_video_s3_key": "evidence/original",
                }
            ],
        )
        upload = AsyncMock()
        with patch.object(candidate_service.s3_service, "upload", upload):
            await candidate_service.upload_malpractice_media(
                db, str(sub["_id"]), 0, str(candidate_user["_id"]), video_bytes=b"benign"
            )
        upload.assert_not_called()
        doc = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["malpractice_events"][0]["screen_video_s3_key"] == "evidence/original"

    async def test_empty_slot_is_filled_and_released_on_s3_failure(
        self, db, assessment, candidate_user
    ):
        sub = await _make_submission(
            db,
            assessment,
            candidate_user,
            malpractice_events=[{"type": "tab_switch", "round": 1, "timestamp": utcnow()}],
        )
        with patch.object(
            candidate_service.s3_service, "upload", AsyncMock(side_effect=RuntimeError("s3 down"))
        ):
            with pytest.raises(RuntimeError):
                await candidate_service.upload_malpractice_media(
                    db, str(sub["_id"]), 0, str(candidate_user["_id"]), video_bytes=b"clip"
                )
        doc = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["malpractice_events"][0].get("screen_video_s3_key") is None
        # slot is free again → a retry succeeds
        with patch.object(candidate_service.s3_service, "upload", AsyncMock()):
            await candidate_service.upload_malpractice_media(
                db, str(sub["_id"]), 0, str(candidate_user["_id"]), video_bytes=b"clip"
            )
        doc = await db.assessment_submissions.find_one({"_id": sub["_id"]})
        assert doc["malpractice_events"][0]["screen_video_s3_key"]


class TestShareLinkEnforcement:
    async def test_new_attempt_outside_window_is_rejected(self, db, assessment, candidate_user):
        now = utcnow()
        await db.assessment_shares.insert_one(
            {
                "assessment_id": assessment["_id"],
                "share_link": "win-1",
                "is_active": True,
                "start_time": now - timedelta(days=2),
                "end_time": now - timedelta(days=1),
            }
        )
        with pytest.raises(ForbiddenException):
            await candidate_service.start_assessment(db, "win-1", str(candidate_user["_id"]))

    async def test_public_assessment_hides_internal_fields(self, db, assessment):
        await db.assessments.update_one(
            {"_id": assessment["_id"]},
            {"$set": {"restricted_emails": ["a@b.c"], "restriction_mode": "INCLUDE"}},
        )
        data = await candidate_service.get_candidate_assessment(db, "tl-1")
        for hidden in ("share_link", "restricted_emails", "restriction_mode", "created_by"):
            assert hidden not in data

    async def test_revoked_nonce_link_does_not_fall_back_to_signature(self, db, assessment):
        with patch.object(
            candidate_service,
            "decode_sharelink",
            return_value={"a": str(assessment["_id"]), "n": "x"},
        ):
            asm, share = await candidate_service._resolve_assessment_and_share(db, "signed-link")
        assert asm is None and share is None
