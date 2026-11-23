"""Security checks that need a REAL MongoDB (aggregation scoping, true atomic races).

docker run -d -p 27019:27017 mongo:7.0
MONGO_TEST_URL=mongodb://127.0.0.1:27019 pytest test/test_security_mongo.py
"""

import asyncio
import os

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.common.constants.app_constants import SubmissionStatus, UserRole
from app.common.exceptions import ForbiddenException, UnauthorizedException
from app.common.utils import utcnow
from app.components.auth import auth_service
from app.components.candidate import candidate_service
from app.components.users import user_service

URL = os.getenv("MONGO_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MONGO_TEST_URL not set")
PW = "Passw0rd!x"  # NOSONAR


@pytest.fixture
async def rdb():
    client = AsyncIOMotorClient(URL)
    name = f"talentia_sec_{ObjectId()}"
    yield client[name]
    await client.drop_database(name)
    client.close()


async def _mk_user(db, email, role, ws=None):
    d = {
        "first_name": "F",
        "last_name": "L",
        "email": email,
        "password_hash": auth_service.hash_password(PW),
        "role": role,
        "is_active": True,
        "workspace_ids": ws or [],
        "created_at": utcnow(),
    }
    d["_id"] = (await db.users.insert_one(d)).inserted_id
    return d


class TestLiveInterviewsScope:
    async def test_admin_only_sees_their_workspace(self, rdb):
        ws_a, ws_b = ObjectId(), ObjectId()
        subs = {}
        for key, ws in (("a", ws_a), ("b", ws_b)):
            cand = await _mk_user(rdb, f"{key}@c.com", UserRole.CANDIDATE)
            asm = (await rdb.assessments.insert_one({"workspace_id": ws, "name": key})).inserted_id
            subs[key] = (
                await rdb.assessment_submissions.insert_one(
                    {
                        "assessment_id": asm,
                        "candidate_id": cand["_id"],
                        "status": "in_progress",
                        "started_at": utcnow(),
                        "rounds_data": [],
                    }
                )
            ).inserted_id

        async def ids(workspace_ids):
            res = await candidate_service.get_live_interviews(
                rdb, None, None, "started_at", "desc", 1, 50, workspace_ids
            )
            return {i["submission_id"] for i in res["live_interviews"]}

        assert await ids([str(ws_a)]) == {str(subs["a"])}
        assert await ids([str(ws_b)]) == {str(subs["b"])}
        assert await ids([]) == set()  # admin with no workspace sees nothing
        assert await ids(None) == {str(subs["a"]), str(subs["b"])}  # super admin


class TestAtomicRaces:
    async def test_parallel_setup_creates_one_super_admin(self, rdb):
        res = await asyncio.gather(
            *(
                auth_service.setup_super_admin(
                    rdb, {"first_name": "Su", "email": f"s{i}@x.com", "password": PW}
                )
                for i in range(8)
            ),
            return_exceptions=True,
        )
        assert sum(1 for r in res if not isinstance(r, Exception)) == 1
        assert all(isinstance(r, ForbiddenException) for r in res if isinstance(r, Exception))
        assert await rdb.users.count_documents({"role": UserRole.SUPER_ADMIN}) == 1

    async def test_refresh_token_is_single_use_under_concurrency(self, rdb):
        await _mk_user(rdb, "r@x.com", UserRole.CANDIDATE)
        tokens = await auth_service.candidate_login(rdb, "r@x.com", PW)
        res = await asyncio.gather(
            *(auth_service.refresh_access_token(rdb, tokens.refresh_token) for _ in range(10)),
            return_exceptions=True,
        )
        assert sum(1 for r in res if not isinstance(r, Exception)) == 1
        assert all(isinstance(r, UnauthorizedException) for r in res if isinstance(r, Exception))
        assert await rdb.refresh_tokens.count_documents({}) == 1  # only the replacement


class TestCandidateScopeReal:
    async def test_scope_uses_workspace_assessments(self, rdb):
        ws_a, ws_b = ObjectId(), ObjectId()
        admin = await _mk_user(rdb, "adm@x.com", UserRole.ADMIN, [str(ws_a)])
        admin["_id"] = str(admin["_id"])
        mine = await _mk_user(rdb, "mine@x.com", UserRole.CANDIDATE)
        theirs = await _mk_user(rdb, "theirs@x.com", UserRole.CANDIDATE)
        for cand, ws in ((mine, ws_a), (theirs, ws_b)):
            asm = (await rdb.assessments.insert_one({"workspace_id": ws})).inserted_id
            await rdb.assessment_submissions.insert_one(
                {
                    "assessment_id": asm,
                    "candidate_id": cand["_id"],
                    "status": SubmissionStatus.COMPLETED,
                }
            )
        out = await user_service.list_candidates(rdb, scope_user=admin, page_size=50)
        assert {c["email"] for c in out["candidates"]} == {"mine@x.com"}
        out = await user_service.list_candidates(
            rdb, search="x.com", is_active=True, scope_user=admin
        )
        assert {c["email"] for c in out["candidates"]} == {"mine@x.com"}  # filters AND with scope
