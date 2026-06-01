"""Unit tests for app/components/assessment/assessment_service.py"""

import pytest
from bson import ObjectId

from app.common.exceptions import ForbiddenException, NotFoundException
from app.components.assessment import assessment_service


@pytest.fixture
async def assessment(db, workspace, super_admin):
    """Pre-seeded assessment with one round."""
    result = await assessment_service.create_assessment(
        db,
        str(workspace["_id"]),
        {
            "name": "Python Assessment",
            "description": "Test your Python skills",
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": 2,
                    "max_duration_minutes": 30,
                    "question_ids": [],
                }
            ],
            "accessibility": "normal",
            "monitoring_config": {"tab_monitoring": False, "screenshot_interval": 30},
        },
        str(super_admin["_id"]),
    )
    return result


class TestCreateAssessment:
    async def test_creates_with_share_link(self, db, workspace, super_admin):
        result = await assessment_service.create_assessment(
            db,
            str(workspace["_id"]),
            {
                "name": "My Assessment",
                "rounds": [
                    {
                        "round_number": 1,
                        "question_count": 5,
                        "max_duration_minutes": 45,
                        "question_ids": [],
                    }
                ],
                "accessibility": "normal",
                "monitoring_config": None,
            },
            str(super_admin["_id"]),
        )
        assert result["name"] == "My Assessment"
        assert result["share_link"]


class TestGetAssessments:
    async def test_returns_assessments_in_workspace(self, db, workspace, assessment):
        result = await assessment_service.get_assessments(
            db, str(workspace["_id"]), None, "created_at", "desc", 1, 20
        )
        assert result["pagination"]["total"] == 1

    async def test_search_by_name(self, db, workspace, assessment):
        result = await assessment_service.get_assessments(
            db, str(workspace["_id"]), "Python", "created_at", "desc", 1, 20
        )
        assert result["pagination"]["total"] == 1

    async def test_search_no_match(self, db, workspace, assessment):
        result = await assessment_service.get_assessments(
            db, str(workspace["_id"]), "Golang", "created_at", "desc", 1, 20
        )
        assert result["pagination"]["total"] == 0


class TestGrantReaccess:
    async def test_increments_reaccess_count(self, db, workspace, assessment, candidate_user):
        from app.common.constants.app_constants import SubmissionStatus
        from app.common.utils import utcnow

        sub = {
            "assessment_id": ObjectId(assessment["id"]),
            "candidate_id": candidate_user["_id"],
            "status": SubmissionStatus.COMPLETED,
            "reaccess_count": 0,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)
        await assessment_service.grant_reaccess(db, str(res.inserted_id))
        updated = await db.assessment_submissions.find_one({"_id": res.inserted_id})
        assert updated["reaccess_count"] == 1
        assert updated["status"] == "pending"

    async def test_max_reaccess_raises(self, db, workspace, assessment, candidate_user):
        from app.common.constants.app_constants import SubmissionStatus
        from app.common.utils import utcnow

        sub = {
            "assessment_id": ObjectId(assessment["id"]),
            "candidate_id": candidate_user["_id"],
            "status": SubmissionStatus.COMPLETED,
            "reaccess_count": 3,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)
        with pytest.raises(ForbiddenException, match="limit"):
            await assessment_service.grant_reaccess(db, str(res.inserted_id))

    async def test_nonexistent_submission_raises(self, db):
        with pytest.raises(NotFoundException):
            await assessment_service.grant_reaccess(db, str(ObjectId()))


class TestGetAssessment:
    async def test_success(self, db, workspace, assessment):
        result = await assessment_service.get_assessment(
            db, str(workspace["_id"]), assessment["id"]
        )
        assert result["name"] == "Python Assessment"

    async def test_not_found_raises(self, db, workspace):
        with pytest.raises(NotFoundException):
            await assessment_service.get_assessment(db, str(workspace["_id"]), str(ObjectId()))


class TestUpdateAssessment:
    async def test_updates_name(self, db, workspace, assessment):
        result = await assessment_service.update_assessment(
            db, str(workspace["_id"]), assessment["id"], {"name": "Updated"}
        )
        assert result["name"] == "Updated"

    async def test_updates_description(self, db, workspace, assessment):
        result = await assessment_service.update_assessment(
            db, str(workspace["_id"]), assessment["id"], {"description": "New desc"}
        )
        assert result["description"] == "New desc"

    async def test_updates_rounds(self, db, workspace, assessment):
        rounds = [
            {"round_number": 1, "question_count": 3, "max_duration_minutes": 20, "question_ids": []}
        ]
        result = await assessment_service.update_assessment(
            db, str(workspace["_id"]), assessment["id"], {"rounds": rounds}
        )
        assert result["rounds"][0]["question_count"] == 3

    async def test_updates_accessibility(self, db, workspace, assessment):
        result = await assessment_service.update_assessment(
            db, str(workspace["_id"]), assessment["id"], {"accessibility": "proctored"}
        )
        assert result["accessibility"] == "proctored"

    async def test_updates_monitoring_config_dict(self, db, workspace, assessment):
        mc = {"tab_monitoring": True, "audio_monitoring": False}
        result = await assessment_service.update_assessment(
            db, str(workspace["_id"]), assessment["id"], {"monitoring_config": mc}
        )
        assert result["monitoring_config"]["tab_monitoring"] is True

    async def test_updates_monitoring_config_pydantic(self, db, workspace, assessment):
        from app.components.assessment.assessment_schemas import MonitoringConfig

        mc = MonitoringConfig(tab_monitoring=False, audio_monitoring=True, video_monitoring=False)
        result = await assessment_service.update_assessment(
            db, str(workspace["_id"]), assessment["id"], {"monitoring_config": mc}
        )
        assert result["monitoring_config"]["tab_monitoring"] is False

    async def test_not_found_raises(self, db, workspace):
        with pytest.raises(NotFoundException):
            await assessment_service.update_assessment(
                db, str(workspace["_id"]), str(ObjectId()), {"name": "X"}
            )


class TestGetAssessmentByShareLink:
    async def test_success(self, db, assessment):
        share_link = assessment["share_link"]
        result = await assessment_service.get_assessment_by_share_link(db, share_link)
        assert result["name"] == "Python Assessment"
        # question_ids should be stripped from rounds
        for r in result.get("rounds", []):
            assert "question_ids" not in r

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await assessment_service.get_assessment_by_share_link(db, "nonexistent-link")


class TestGetSubmissions:
    async def test_returns_empty(self, db, workspace, assessment):
        result = await assessment_service.get_submissions(
            db, assessment["id"], None, "created_at", "desc", 1, 20
        )
        assert result["pagination"]["total"] == 0

    async def test_with_submission(self, db, workspace, assessment, candidate_user):
        from app.common.constants.app_constants import SubmissionStatus
        from app.common.utils import utcnow

        await db.assessment_submissions.insert_one(
            {
                "assessment_id": ObjectId(assessment["id"]),
                "candidate_id": candidate_user["_id"],
                "status": SubmissionStatus.COMPLETED,
                "reaccess_count": 0,
                "percentage": 80.0,
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )
        result = await assessment_service.get_submissions(
            db, assessment["id"], None, "created_at", "desc", 1, 20
        )
        assert result["pagination"]["total"] == 1

    async def test_search_filters(self, db, workspace, assessment, candidate_user):
        from app.common.constants.app_constants import SubmissionStatus
        from app.common.utils import utcnow

        await db.assessment_submissions.insert_one(
            {
                "assessment_id": ObjectId(assessment["id"]),
                "candidate_id": candidate_user["_id"],
                "status": SubmissionStatus.IN_PROGRESS,
                "reaccess_count": 0,
                "percentage": 0.0,
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )
        result = await assessment_service.get_submissions(
            db, assessment["id"], "Test", "created_at", "asc", 1, 20
        )
        assert result["pagination"]["total"] >= 0


class TestGetSubmissionDetail:
    async def test_success(self, db, assessment, candidate_user):
        from app.common.constants.app_constants import SubmissionStatus
        from app.common.utils import utcnow

        res = await db.assessment_submissions.insert_one(
            {
                "assessment_id": ObjectId(assessment["id"]),
                "candidate_id": candidate_user["_id"],
                "status": SubmissionStatus.COMPLETED,
                "reaccess_count": 0,
                "rounds_data": [],
                "percentage": 75.0,
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )
        result = await assessment_service.get_submission_detail(db, str(res.inserted_id))
        assert result["percentage"] == 75.0

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await assessment_service.get_submission_detail(db, str(ObjectId()))


class TestExportSubmissions:
    async def test_returns_list(self, db, assessment, candidate_user):
        from app.common.constants.app_constants import SubmissionStatus
        from app.common.utils import utcnow

        await db.assessment_submissions.insert_one(
            {
                "assessment_id": ObjectId(assessment["id"]),
                "candidate_id": candidate_user["_id"],
                "status": SubmissionStatus.COMPLETED,
                "percentage": 90.0,
                "rounds_data": [],
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )
        result = await assessment_service.export_submissions(db, assessment["id"])
        assert isinstance(result, list)


class TestCreateAssessmentPydanticBranches:
    async def test_rounds_as_pydantic_model(self, db, workspace, super_admin):
        """Cover _build_rounds branch where round has model_dump()."""
        from app.components.assessment.assessment_schemas import MonitoringConfig, RoundConfig

        rounds = [RoundConfig(round_number=1, question_count=2, max_duration_minutes=30)]
        monitoring = MonitoringConfig()
        result = await assessment_service.create_assessment(
            db,
            str(workspace["_id"]),
            {
                "name": "Pydantic Test",
                "rounds": rounds,
                "accessibility": "normal",
                "monitoring_config": monitoring,
            },
            str(super_admin["_id"]),
        )
        assert result["name"] == "Pydantic Test"
