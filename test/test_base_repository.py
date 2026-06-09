"""Tests for app.common.repositories.base_repository.BaseRepository."""

import pytest
from bson import ObjectId

from app.common.exceptions import NotFoundException
from app.common.repositories.base_repository import BaseRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repo(db):
    return BaseRepository(db, "widgets")


async def test_insert_and_find_by_id(repo):
    doc = await repo.insert_one({"name": "alpha"})
    assert doc["name"] == "alpha"
    assert "id" in doc
    fetched = await repo.find_by_id(doc["id"])
    assert fetched["name"] == "alpha"


async def test_find_by_id_missing(repo):
    assert await repo.find_by_id(str(ObjectId())) is None


async def test_find_by_id_or_raise_ok(repo):
    doc = await repo.insert_one({"name": "x"})
    fetched = await repo.find_by_id_or_raise(doc["id"])
    assert fetched["id"] == doc["id"]


async def test_find_by_id_or_raise_missing(repo):
    with pytest.raises(NotFoundException):
        await repo.find_by_id_or_raise(str(ObjectId()))


async def test_find_one_and_or_raise(repo):
    await repo.insert_one({"name": "findme", "tag": "t"})
    found = await repo.find_one({"tag": "t"})
    assert found["name"] == "findme"
    assert await repo.find_one({"tag": "nope"}) is None
    with pytest.raises(NotFoundException):
        await repo.find_one_or_raise({"tag": "nope"}, "custom msg")


async def test_find_many_with_sort_skip_limit(repo):
    for i in range(5):
        await repo.insert_one({"name": f"n{i}", "order": i})
    docs = await repo.find_many({}, sort=[("order", -1)], skip=1, limit=2)
    assert len(docs) == 2
    assert docs[0]["order"] == 3


async def test_count(repo):
    await repo.insert_one({"name": "a"})
    await repo.insert_one({"name": "b"})
    assert await repo.count({}) == 2


async def test_find_paginated(repo):
    for i in range(3):
        await repo.insert_one({"name": f"n{i}", "created_at": i})
    total, docs = await repo.find_paginated(
        {}, "created_at", 1, 0, 10, allowed_sort_fields=["created_at"]
    )
    assert total == 3
    assert len(docs) == 3


async def test_find_paginated_invalid_sort_field_falls_back(repo):
    await repo.insert_one({"name": "a"})
    total, docs = await repo.find_paginated(
        {}, "evil_field", 1, 0, 10, allowed_sort_fields=["name"]
    )
    assert total == 1


async def test_update_by_id(repo):
    doc = await repo.insert_one({"name": "old"})
    updated = await repo.update_by_id(doc["id"], {"name": "new"})
    assert updated["name"] == "new"
    assert "updated_at" in updated


async def test_soft_delete(repo):
    doc = await repo.insert_one({"name": "x", "is_active": True})
    await repo.soft_delete(doc["id"])
    fetched = await repo.find_by_id(doc["id"])
    assert fetched["is_active"] is False


async def test_soft_delete_missing_raises(repo):
    with pytest.raises(NotFoundException):
        await repo.soft_delete(str(ObjectId()))


async def test_hard_delete(repo):
    doc = await repo.insert_one({"name": "x"})
    await repo.hard_delete(doc["id"])
    assert await repo.find_by_id(doc["id"]) is None


async def test_hard_delete_missing_raises(repo):
    with pytest.raises(NotFoundException):
        await repo.hard_delete(str(ObjectId()))


async def test_build_search_filter(repo):
    f = repo.build_search_filter("name", "abc")
    assert f["name"]["$regex"] == "abc"
    assert f["name"]["$options"] == "i"


async def test_build_multi_field_search(repo):
    f = repo.build_multi_field_search(["a", "b"], "term")
    assert "$or" in f
    assert len(f["$or"]) == 2


async def test_exists(repo):
    await repo.insert_one({"name": "x"})
    assert await repo.exists({"name": "x"}) is True
    assert await repo.exists({"name": "nope"}) is False
