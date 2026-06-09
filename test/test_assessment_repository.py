"""Tests for app.components.assessment.assessment_repository.AssessmentRepository."""

import pytest
from bson import ObjectId

from app.common.exceptions import NotFoundException
from app.components.assessment.assessment_repository import AssessmentRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repo(db):
    return AssessmentRepository(db)


async def _make_assessment(db, workspace_id):
    doc = {
        "name": "Backend",
        "workspace_id": ObjectId(workspace_id),
        "is_active": True,
    }
    result = await db.assessments.insert_one(doc)
    return str(result.inserted_id)


async def test_create_backfills_sharelink(repo):
    doc = await repo.create({"name": "New", "workspace_id": ObjectId()})
    assert "share_link" in doc
    assert doc["share_link"]


async def test_find_active_by_id_ok(repo, db):
    wid = str(ObjectId())
    aid = await _make_assessment(db, wid)
    found = await repo.find_active_by_id(wid, aid)
    assert found["name"] == "Backend"


async def test_find_active_by_id_missing(repo):
    with pytest.raises(NotFoundException):
        await repo.find_active_by_id(str(ObjectId()), str(ObjectId()))


async def test_find_by_sharelink(repo, db):
    await db.assessments.insert_one({"name": "X", "share_link": "abc", "is_active": True})
    found = await repo.find_by_sharelink("abc")
    assert found["name"] == "X"
    assert await repo.find_by_sharelink("missing") is None


async def test_list_paginated_with_counts(repo, db):
    wid = str(ObjectId())
    aid = await _make_assessment(db, wid)
    await db.assessment_submissions.insert_one({"assessment_id": ObjectId(aid)})
    total, docs = await repo.list_paginated_with_counts(wid, None, "created_at", -1, 0, 10)
    assert total == 1
    assert docs[0]["submission_count"] == 1


async def test_list_paginated_with_search(repo, db):
    wid = str(ObjectId())
    await _make_assessment(db, wid)
    total, docs = await repo.list_paginated_with_counts(wid, "Back", "name", 1, 0, 10)
    assert total == 1


async def test_update(repo, db):
    wid = str(ObjectId())
    aid = await _make_assessment(db, wid)
    updated = await repo.update(wid, aid, {"name": "Renamed"})
    assert updated["name"] == "Renamed"


async def test_update_missing(repo):
    with pytest.raises(NotFoundException):
        await repo.update(str(ObjectId()), str(ObjectId()), {"name": "x"})


async def test_delete(repo, db):
    wid = str(ObjectId())
    aid = await _make_assessment(db, wid)
    await repo.delete(wid, aid)
    doc = await db.assessments.find_one({"_id": ObjectId(aid)})
    assert doc["is_active"] is False


async def test_delete_missing(repo):
    with pytest.raises(NotFoundException):
        await repo.delete(str(ObjectId()), str(ObjectId()))


async def test_get_submission(repo, db):
    result = await db.assessment_submissions.insert_one({"status": "completed"})
    sub = await repo.get_submission(str(result.inserted_id))
    assert sub["status"] == "completed"


async def test_get_submission_missing(repo):
    with pytest.raises(NotFoundException):
        await repo.get_submission(str(ObjectId()))


async def test_count_submissions(repo, db):
    aid = str(ObjectId())
    await db.assessment_submissions.insert_one({"assessment_id": ObjectId(aid)})
    assert await repo.count_submissions(aid) == 1
