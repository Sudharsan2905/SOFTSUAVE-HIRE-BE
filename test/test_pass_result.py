"""Pass percentage on assessments and the derived (never stored) pass/fail result."""

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.common.constants.app_constants import DEFAULT_PASS_PERCENTAGE
from app.common.utils import utcnow
from app.components.assessment import assessment_service
from app.components.assessment.assessment_schemas import (
    CreateAssessmentRequest,
    UpdateAssessmentRequest,
)
from app.components.assessment.result import (
    effective_pass_percentage,
    evaluate_result,
    result_query,
)

ROUND = {"round_number": 1, "question_count": 2, "max_duration_minutes": 30, "question_ids": []}


def _payload(**extra):
    return {
        "name": "Pass Mark Assessment",
        "description": "",
        "rounds": [ROUND],
        "accessibility": "normal",
        "monitoring_config": None,
        **extra,
    }


async def _create(db, workspace, super_admin, **extra):
    return await assessment_service.create_assessment(
        db, str(workspace["_id"]), _payload(**extra), str(super_admin["_id"])
    )


def _submission(assessment_id, candidate_id, **overrides):
    now = utcnow()
    doc = {
        "assessment_id": ObjectId(assessment_id),
        "candidate_id": candidate_id,
        "status": "completed",
        "scoring_status": "scored",
        "percentage": 70.0,
        "rounds_data": [],
        "is_active": True,
        "started_at": now,
        "created_at": now,
        "updated_at": now,
    }
    doc.update(overrides)
    return doc


class TestSchemaValidation:
    def test_optional_on_create(self):
        req = CreateAssessmentRequest(name="Quiz", rounds=[ROUND])
        assert req.pass_percentage is None

    @pytest.mark.parametrize("value", [0, -5, 101])
    def test_out_of_range_rejected(self, value):
        with pytest.raises(ValidationError):
            CreateAssessmentRequest(name="Quiz", rounds=[ROUND], pass_percentage=value)
        with pytest.raises(ValidationError):
            UpdateAssessmentRequest(pass_percentage=value)

    @pytest.mark.parametrize("value", [1, 50, 100])
    def test_bounds_accepted(self, value):
        assert (
            CreateAssessmentRequest(
                name="Quiz", rounds=[ROUND], pass_percentage=value
            ).pass_percentage
            == value
        )


class TestEvaluateResult:
    def test_default_when_missing(self):
        assert effective_pass_percentage({}) == DEFAULT_PASS_PERCENTAGE == 50
        assert effective_pass_percentage(None) == 50
        assert effective_pass_percentage({"pass_percentage": 65}) == 65

    @pytest.mark.parametrize(
        ("percentage", "expected"), [(49.9, "failed"), (50.0, "passed"), (100.0, "passed")]
    )
    def test_boundary_is_inclusive(self, percentage, expected):
        sub = {"status": "completed", "scoring_status": "scored", "percentage": percentage}
        assert evaluate_result(sub, 50) == expected

    @pytest.mark.parametrize("status", ["in_progress", "on_hold", "malpractice", "terminated"])
    def test_non_completed_has_no_result(self, status):
        sub = {"status": status, "scoring_status": "scored", "percentage": 99.0}
        assert evaluate_result(sub, 50) is None

    @pytest.mark.parametrize("scoring_status", ["pending", "needs_review", "failed"])
    def test_unfinished_scoring_has_no_result(self, scoring_status):
        sub = {"status": "completed", "scoring_status": scoring_status, "percentage": 99.0}
        assert evaluate_result(sub, 50) is None

    def test_legacy_submission_scored_before_scoring_status(self):
        scored = {"status": "completed", "scoring_completed_at": utcnow(), "percentage": 80.0}
        unscored = {"status": "completed", "scoring_completed_at": None, "percentage": 80.0}
        assert evaluate_result(scored, 50) == "passed"
        assert evaluate_result(unscored, 50) is None


class TestLastRoundStillScoring:
    """Right after the final round is finished, scoring_status is still 'scored' from the
    previous round and the percentage is partial: that must not read as a Failed result."""

    STALE = {
        "status": "completed",
        "scoring_status": "scored",
        "percentage": 10.0,
        "rounds_data": [{"round_number": 1, "score": 1}, {"round_number": 2}],
    }

    def test_no_result_until_every_round_is_scored(self):
        assert evaluate_result(self.STALE, 50) is None
        scored = {
            **self.STALE,
            "rounds_data": [{"round_number": 1, "score": 1}, {"round_number": 2, "score": 5}],
        }
        assert evaluate_result(scored, 50) == "failed"
        assert evaluate_result({**scored, "percentage": 80.0}, 50) == "passed"

    async def test_not_counted_in_stats_list_or_export(
        self, db, workspace, super_admin, candidate_user
    ):
        created = await _create(db, workspace, super_admin, pass_percentage=50)
        aid = created["id"]
        await db.assessment_submissions.insert_one(
            _submission(
                aid, candidate_user["_id"], **{k: v for k, v in self.STALE.items() if k != "status"}
            )
        )
        stats = await assessment_service.get_submission_stats(db, aid)
        assert (stats["passed"], stats["failed"]) == (0, 0)

        listed = await assessment_service.get_submissions(
            db, aid, None, "started_at", "desc", 1, 20
        )
        assert listed["submissions"][0]["result"] is None

        rows = await assessment_service.export_submissions(db, aid)
        assert rows[0]["result"] is None
        assert "rounds_data" not in rows[0]


class TestAssessmentPassPercentage:
    async def test_defaults_to_50_when_omitted(self, db, workspace, super_admin):
        created = await _create(db, workspace, super_admin)
        assert created["pass_percentage"] == 50

    async def test_custom_value_is_stored(self, db, workspace, super_admin):
        created = await _create(db, workspace, super_admin, pass_percentage=70)
        assert created["pass_percentage"] == 70
        stored = await db.assessments.find_one({"_id": ObjectId(created["id"])})
        assert stored["pass_percentage"] == 70

    async def test_update_changes_it(self, db, workspace, super_admin):
        created = await _create(db, workspace, super_admin, pass_percentage=70)
        updated = await assessment_service.update_assessment(
            db, str(workspace["_id"]), created["id"], {"pass_percentage": 40}
        )
        assert updated["pass_percentage"] == 40

    async def test_update_without_it_keeps_value(self, db, workspace, super_admin):
        created = await _create(db, workspace, super_admin, pass_percentage=70)
        updated = await assessment_service.update_assessment(
            db, str(workspace["_id"]), created["id"], {"name": "Renamed"}
        )
        assert updated["pass_percentage"] == 70

    async def test_legacy_assessment_reads_as_default(self, db, workspace, super_admin):
        created = await _create(db, workspace, super_admin)
        await db.assessments.update_one(
            {"_id": ObjectId(created["id"])}, {"$unset": {"pass_percentage": ""}}
        )
        fetched = await assessment_service.get_assessment(db, str(workspace["_id"]), created["id"])
        listed = await assessment_service.get_assessments(
            db, str(workspace["_id"]), None, "created_at", "desc", 1, 20
        )
        assert fetched["pass_percentage"] == 50
        assert listed["assessments"][0]["pass_percentage"] == 50


class TestDerivedResults:
    async def _seed(self, db, workspace, super_admin, candidate_user, **sub):
        created = await _create(db, workspace, super_admin, pass_percentage=60)
        res = await db.assessment_submissions.insert_one(
            _submission(created["id"], candidate_user["_id"], **sub)
        )
        return created["id"], str(res.inserted_id)

    async def test_submissions_list_has_result(self, db, workspace, super_admin, candidate_user):
        aid, _ = await self._seed(db, workspace, super_admin, candidate_user, percentage=70.0)
        out = await assessment_service.get_submissions(db, aid, None, "started_at", "desc", 1, 20)
        assert out["pass_percentage"] == 60
        assert out["submissions"][0]["result"] == "passed"

    async def test_changing_pass_mark_reevaluates_everyone(
        self, db, workspace, super_admin, candidate_user
    ):
        aid, sid = await self._seed(db, workspace, super_admin, candidate_user, percentage=70.0)
        first = await assessment_service.get_submission_detail(db, sid)
        assert first["result"] == "passed"

        await assessment_service.update_assessment(
            db, str(workspace["_id"]), aid, {"pass_percentage": 80}
        )
        second = await assessment_service.get_submission_detail(db, sid)
        assert second["result"] == "failed"
        assert second["pass_percentage"] == 80
        # nothing is persisted on the submission itself
        stored = await db.assessment_submissions.find_one({"_id": ObjectId(sid)})
        assert "result" not in stored

    async def test_malpractice_session_has_no_result(
        self, db, workspace, super_admin, candidate_user
    ):
        _, sid = await self._seed(
            db, workspace, super_admin, candidate_user, status="malpractice", percentage=90.0
        )
        detail = await assessment_service.get_submission_detail(db, sid)
        assert detail["result"] is None

    async def test_stats_count_passed_and_failed(self, db, workspace, super_admin, candidate_user):
        aid, _ = await self._seed(db, workspace, super_admin, candidate_user, percentage=70.0)
        for pct, status in [(40.0, "completed"), (95.0, "malpractice"), (80.0, "in_progress")]:
            await db.assessment_submissions.insert_one(
                _submission(aid, ObjectId(), percentage=pct, status=status)
            )
        stats = await assessment_service.get_submission_stats(db, aid)
        assert (stats["pass_percentage"], stats["passed"], stats["failed"]) == (60, 1, 1)

    async def test_export_has_result(self, db, workspace, super_admin, candidate_user):
        aid, _ = await self._seed(db, workspace, super_admin, candidate_user, percentage=30.0)
        rows = await assessment_service.export_submissions(db, aid)
        assert rows[0]["result"] == "failed"
        assert "scoring_status" not in rows[0]

    def test_result_query_matches_complement(self):
        passed, failed = result_query(60, True), result_query(60, False)
        assert passed["percentage"] == {"$gte": 60}
        assert failed["percentage"] == {"$lt": 60}


class TestCandidateNeverSeesPassMark:
    async def test_public_assessment_view_hides_it(
        self, db, workspace, super_admin, candidate_user
    ):
        from app.components.candidate import candidate_service

        created = await _create(db, workspace, super_admin, pass_percentage=70)
        view = await candidate_service.get_candidate_assessment(db, created["share_link"])
        assert "pass_percentage" not in view
        assert "result" not in view


class TestResultFilter:
    """The Passed / Failed filter on the View Details submissions list (and the export)."""

    @staticmethod
    async def _seed(db, workspace, super_admin):
        created = await _create(db, workspace, super_admin, pass_percentage=60)
        aid = created["id"]
        rows = {
            "passed_high": {"percentage": 92.0},
            "passed_edge": {"percentage": 60.0},
            "failed_low": {"percentage": 35.0},
            "failed_edge": {"percentage": 59.9},
            "malpractice": {"percentage": 95.0, "status": "malpractice"},
            "in_progress": {"percentage": 70.0, "status": "in_progress"},
            "needs_review": {"percentage": 80.0, "scoring_status": "needs_review"},
        }
        ids = {}
        for name, over in rows.items():
            user = await db.users.insert_one(
                {"first_name": name, "last_name": "T", "email": f"{name}@example.com"}
            )
            doc = _submission(aid, user.inserted_id, **over)
            ids[name] = (await db.assessment_submissions.insert_one(doc)).inserted_id
        return aid, ids

    @staticmethod
    def _listed(out):
        return {s["percentage"] for s in out["submissions"]}

    async def test_passed_only(self, db, workspace, super_admin):
        aid, _ = await self._seed(db, workspace, super_admin)
        out = await assessment_service.get_submissions(
            db, aid, None, "started_at", "desc", 1, 20, result="passed"
        )
        assert self._listed(out) == {92.0, 60.0}
        assert {s["result"] for s in out["submissions"]} == {"passed"}
        assert out["pagination"]["total"] == 2

    async def test_failed_only(self, db, workspace, super_admin):
        aid, _ = await self._seed(db, workspace, super_admin)
        out = await assessment_service.get_submissions(
            db, aid, None, "started_at", "desc", 1, 20, result="failed"
        )
        assert self._listed(out) == {35.0, 59.9}
        assert {s["result"] for s in out["submissions"]} == {"failed"}
        assert out["pagination"]["total"] == 2

    async def test_no_filter_returns_everyone(self, db, workspace, super_admin):
        aid, _ = await self._seed(db, workspace, super_admin)
        out = await assessment_service.get_submissions(db, aid, None, "started_at", "desc", 1, 20)
        assert out["pagination"]["total"] == 7

    async def test_combines_with_status_filter(self, db, workspace, super_admin):
        aid, _ = await self._seed(db, workspace, super_admin)
        completed = await assessment_service.get_submissions(
            db, aid, None, "started_at", "desc", 1, 20, status="completed", result="passed"
        )
        assert completed["pagination"]["total"] == 2
        # a result filter can never surface an unfinished session
        running = await assessment_service.get_submissions(
            db, aid, None, "started_at", "desc", 1, 20, status="in_progress", result="passed"
        )
        assert running["pagination"]["total"] == 0

    async def test_follows_the_current_pass_mark(self, db, workspace, super_admin):
        aid, _ = await self._seed(db, workspace, super_admin)
        await assessment_service.update_assessment(
            db, str(workspace["_id"]), aid, {"pass_percentage": 90}
        )
        passed = await assessment_service.get_submissions(
            db, aid, None, "started_at", "desc", 1, 20, result="passed"
        )
        assert self._listed(passed) == {92.0}

    async def test_export_respects_the_filter(self, db, workspace, super_admin):
        aid, _ = await self._seed(db, workspace, super_admin)
        failed = await assessment_service.export_submissions(db, aid, result="failed")
        assert {r["percentage"] for r in failed} == {35.0, 59.9}
        assert {r["result"] for r in failed} == {"failed"}
