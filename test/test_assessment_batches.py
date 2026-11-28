"""Batch management: default Batch-1, create_batch, assignment and submission filtering."""

import pytest
from bson import ObjectId

from app.common.exceptions import ConflictException, NotFoundException
from app.common.utils import utcnow
from app.components.assessment import assessment_service


@pytest.fixture
async def assessment(db, workspace, super_admin):
    return await assessment_service.create_assessment(
        db,
        str(workspace["_id"]),
        {
            "name": "Batch Assessment",
            "rounds": [
                {"round_number": 1, "question_count": 1, "max_duration_minutes": 10},
            ],
            "accessibility": "normal",
        },
        str(super_admin["_id"]),
    )


async def _add_submission(db, assessment_id, candidate_id, **extra):
    await db.assessment_submissions.insert_one(
        {
            "assessment_id": ObjectId(assessment_id),
            "candidate_id": candidate_id,
            "status": "completed",
            "rounds_data": [],
            "created_at": utcnow(),
            "updated_at": utcnow(),
            **extra,
        }
    )


class TestBatches:
    async def test_create_assessment_adds_default_batch(self, assessment):
        assert len(assessment["batch"]) == 1
        assert assessment["batch"][0]["bName"] == "Batch-1"
        assert assessment["batch"][0]["bId"]

    async def test_create_batch_appends_with_unique_id(self, db, workspace, assessment):
        created = await assessment_service.create_batch(
            db, str(workspace["_id"]), assessment["id"], "  Batch-2 "
        )
        doc = await db.assessments.find_one({"_id": ObjectId(assessment["id"])})
        assert [b["bName"] for b in doc["batch"]] == ["Batch-1", "Batch-2"]
        assert created["bId"] != doc["batch"][0]["bId"]

    async def test_duplicate_name_rejected(self, db, workspace, assessment):
        with pytest.raises(ConflictException):
            await assessment_service.create_batch(
                db, str(workspace["_id"]), assessment["id"], "batch-1"
            )

    async def test_unknown_assessment(self, db, workspace):
        with pytest.raises(NotFoundException):
            await assessment_service.create_batch(
                db, str(workspace["_id"]), str(ObjectId()), "Batch-2"
            )

    async def test_legacy_assessment_gets_default_and_backfill(
        self, db, workspace, assessment, candidate_user
    ):
        oid = ObjectId(assessment["id"])
        await db.assessments.update_one({"_id": oid}, {"$unset": {"batch": ""}})
        await _add_submission(db, assessment["id"], candidate_user["_id"])

        detail = await assessment_service.get_assessment(
            db, str(workspace["_id"]), assessment["id"]
        )
        bid = detail["batch"][0]["bId"]
        sub = await db.assessment_submissions.find_one({"assessment_id": oid})
        assert detail["batch"][0]["bName"] == "Batch-1"
        assert sub["batch"] == bid

    async def test_submissions_filtered_by_batch(self, db, assessment, candidate_user):
        await _add_submission(db, assessment["id"], candidate_user["_id"], batch="b1")
        await _add_submission(db, assessment["id"], ObjectId(), batch="b2")
        data = await assessment_service.get_submissions(
            db, assessment["id"], None, "created_at", "desc", 1, 20, batch="b2"
        )
        assert data["pagination"]["total"] == 1
        assert data["submissions"][0]["batch"] == "b2"
