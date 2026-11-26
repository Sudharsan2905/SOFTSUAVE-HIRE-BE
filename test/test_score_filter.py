"""Score Filter: combined score across several assessments judged against one pass mark."""

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.common.constants.app_constants import UserRole
from app.common.exceptions import NotFoundException, ValidationException
from app.common.response_models.assessment_responses import ScoreFilterResponse
from app.common.utils import utcnow
from app.components.assessment import assessment_service, score_filter_service
from app.components.assessment.access import verify_assessments_in_workspace
from app.components.auth.auth_service import create_access_token, hash_password
from app.core.dependencies import get_db
from app.factory import create_application

ROUND = {"round_number": 1, "question_count": 2, "max_duration_minutes": 30, "question_ids": []}


# ── helpers ──────────────────────────────────────────────────────────────────


async def _assessment(db, workspace, super_admin, name):
    return await assessment_service.create_assessment(
        db,
        str(workspace["_id"]),
        {
            "name": name,
            "description": "",
            "rounds": [ROUND],
            "accessibility": "normal",
            "monitoring_config": None,
        },
        str(super_admin["_id"]),
    )


async def _candidate(db, first, last=""):
    email = f"{first}.{last or 'x'}@example.com".lower()
    res = await db.users.insert_one(
        {"first_name": first, "last_name": last, "email": email, "role": UserRole.CANDIDATE.value}
    )
    return res.inserted_id


async def _sub(db, assessment_id, candidate_id, correct, total, **overrides):
    """A completed, fully scored submission with ``correct`` of ``total`` questions right."""
    now = utcnow()
    doc = {
        "assessment_id": ObjectId(assessment_id),
        "candidate_id": candidate_id,
        "status": "completed",
        "scoring_status": "scored",
        "percentage": round(correct / total * 100, 1) if total else 0.0,
        "rounds_data": [{"score": correct, "questions": [{"id": str(i)} for i in range(total)]}],
        "is_active": True,
        "started_at": now,
        "created_at": now,
        "updated_at": now,
    }
    doc.update(overrides)
    await db.assessment_submissions.insert_one(doc)


async def _run(db, oids, **kw):
    args = {
        "pass_percentage": None,
        "result": "all",
        "search": None,
        "sort_by": "combined_percentage",
        "sort_order": "desc",
        "page": 1,
        "page_size": 20,
        **kw,
    }
    return await score_filter_service.get_score_filter(db, [ObjectId(o) for o in oids], **args)


def _by_name(data):
    return {c["candidate"]["name"]: c for c in data["candidates"]}


@pytest.fixture
async def two(db, workspace, super_admin):
    a = await _assessment(db, workspace, super_admin, "Python")
    b = await _assessment(db, workspace, super_admin, "SQL")
    return a["id"], b["id"]


# ── combined score ───────────────────────────────────────────────────────────


class TestCombinedScore:
    async def test_total_correct_over_total_questions(self, db, two):
        a, b = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 8, 10)  # 80%
        await _sub(db, b, cand, 4, 10)  # 40%

        row = _by_name(await _run(db, [a, b], pass_percentage=60))["Asha K"]

        assert (row["combined_score"], row["combined_total"]) == (12, 20)
        assert row["combined_percentage"] == 60.0
        assert row["completed_count"] == 2

    async def test_boundary_is_inclusive(self, db, two):
        a, b = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 8, 10)
        await _sub(db, b, cand, 4, 10)

        assert _by_name(await _run(db, [a, b], pass_percentage=60))["Asha K"]["result"] == "passed"
        assert (
            _by_name(await _run(db, [a, b], pass_percentage=60.1))["Asha K"]["result"] == "failed"
        )

    async def test_weighted_by_question_count_not_averaged(self, db, two):
        a, b = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 9, 10)  # 90%
        await _sub(db, b, cand, 10, 40)  # 25%  -> average would be 57.5, combined is 38.0

        row = _by_name(await _run(db, [a, b]))["Asha K"]

        assert row["combined_percentage"] == 38.0

    async def test_single_assessment_matches_its_own_percentage(self, db, two):
        a, _ = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 7, 10)

        row = _by_name(await _run(db, [a], pass_percentage=70))["Asha K"]

        assert row["combined_percentage"] == 70.0
        assert row["result"] == "passed"

    async def test_no_pass_percentage_means_no_result_yet(self, db, two):
        a, b = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 8, 10)
        await _sub(db, b, cand, 8, 10)

        data = await _run(db, [a, b])

        row = _by_name(data)["Asha K"]
        assert row["result"] is None
        assert row["combined_percentage"] == 80.0
        assert data["pass_percentage"] is None
        assert data["summary"]["passed"] == 0
        assert data["summary"]["failed"] == 0
        assert data["summary"]["completed"] == 1

    async def test_no_stored_pass_mark_is_used(self, db, two):
        """The typed percentage decides, whatever the assessments' own pass mark says."""
        a, b = two
        await db.assessments.update_many({}, {"$set": {"pass_percentage": 99}})
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 5, 10)
        await _sub(db, b, cand, 5, 10)

        assert _by_name(await _run(db, [a, b], pass_percentage=50))["Asha K"]["result"] == "passed"


# ── incomplete candidates ────────────────────────────────────────────────────


class TestIncomplete:
    async def test_missing_second_assessment_is_not_attempted(self, db, two):
        a, b = two
        cand = await _candidate(db, "Ravi", "S")
        await _sub(db, a, cand, 10, 10)

        row = _by_name(await _run(db, [a, b], pass_percentage=50))["Ravi S"]

        assert row["result"] == "incomplete"
        assert row["combined_percentage"] is None
        assert row["combined_score"] is None
        assert row["completed_count"] == 1
        states = {item["assessment_id"]: item["status"] for item in row["breakdown"]}
        assert states == {a: "completed", b: "not_attempted"}

    @pytest.mark.parametrize(
        ("status", "extra", "expected"),
        [
            ("in_progress", {}, "in_progress"),
            ("on_hold", {}, "on_hold"),
            ("pending", {}, "pending"),
            ("terminated", {}, "terminated"),
            ("malpractice", {}, "malpractice"),
            ("completed", {"scoring_status": "pending"}, "scoring_pending"),
            ("completed", {"scoring_status": "failed"}, "scoring_pending"),
        ],
    )
    async def test_unfinished_states_are_incomplete(self, db, two, status, extra, expected):
        a, b = two
        cand = await _candidate(db, "Ravi", "S")
        await _sub(db, a, cand, 10, 10)
        await _sub(db, b, cand, 10, 10, status=status, **extra)

        row = _by_name(await _run(db, [a, b], pass_percentage=50))["Ravi S"]

        assert row["result"] == "incomplete"
        assert row["breakdown"][1]["status"] == expected
        assert row["breakdown"][1]["percentage"] is None

    async def test_round_without_a_score_is_not_final(self, db, two):
        a, b = two
        cand = await _candidate(db, "Ravi", "S")
        await _sub(db, a, cand, 10, 10)
        await _sub(
            db, b, cand, 5, 10, rounds_data=[{"score": 5, "questions": [{}]}, {"questions": [{}]}]
        )

        row = _by_name(await _run(db, [a, b]))["Ravi S"]

        assert row["result"] == "incomplete"

    async def test_soft_deleted_submission_counts_as_not_attempted(self, db, two):
        a, b = two
        cand = await _candidate(db, "Ravi", "S")
        await _sub(db, a, cand, 10, 10)
        await _sub(db, b, cand, 10, 10, is_active=False)

        row = _by_name(await _run(db, [a, b]))["Ravi S"]

        assert row["breakdown"][1]["status"] == "not_attempted"

    async def test_reaccess_reset_submission_is_incomplete(self, db, two):
        a, b = two
        cand = await _candidate(db, "Ravi", "S")
        await _sub(db, a, cand, 10, 10)
        # What re-access leaves behind: pending, score reset, fresh unscored rounds.
        await _sub(
            db,
            b,
            cand,
            0,
            10,
            status="pending",
            percentage=0.0,
            rounds_data=[{"questions": [{}] * 10}],
        )

        row = _by_name(await _run(db, [a, b], pass_percentage=50))["Ravi S"]

        assert row["result"] == "incomplete"


# ── filtering, search, sorting, paging, summary ──────────────────────────────


class TestListing:
    @pytest.fixture
    async def crowd(self, db, two):
        a, b = two
        # name -> (python correct, sql correct); 10 questions each, so combined = sum * 5
        for name, py, sql in (
            ("Anil A", 9, 9),  # 90
            ("Bina B", 6, 6),  # 60
            ("Chet C", 3, 3),  # 30
        ):
            cand = await _candidate(db, name.split()[0], name.split()[1])
            await _sub(db, a, cand, py, 10)
            await _sub(db, b, cand, sql, 10)
        half = await _candidate(db, "Dara", "D")
        await _sub(db, a, half, 10, 10)  # no SQL -> incomplete
        return a, b

    async def test_summary_counts_ignore_the_result_filter(self, db, crowd):
        data = await _run(db, list(crowd), pass_percentage=50, result="passed")

        assert data["summary"] == {
            "total": 4,
            "completed": 3,
            "passed": 2,
            "failed": 1,
            "incomplete": 1,
        }
        assert sorted(_by_name(data)) == ["Anil A", "Bina B"]
        assert data["pagination"]["total"] == 2

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            ("all", ["Anil A", "Bina B", "Chet C", "Dara D"]),
            ("passed", ["Anil A", "Bina B"]),
            ("failed", ["Chet C"]),
            ("incomplete", ["Dara D"]),
        ],
    )
    async def test_result_filter(self, db, crowd, result, expected):
        data = await _run(db, list(crowd), pass_percentage=50, result=result)

        assert [c["candidate"]["name"] for c in data["candidates"]] == expected

    async def test_changing_the_percentage_changes_results_without_storing_anything(
        self, db, crowd
    ):
        before = await db.assessment_submissions.find().to_list(None)

        low = await _run(db, list(crowd), pass_percentage=20)
        high = await _run(db, list(crowd), pass_percentage=95)

        assert low["summary"]["passed"] == 3
        assert high["summary"]["passed"] == 0
        assert await db.assessment_submissions.find().to_list(None) == before

    @pytest.mark.parametrize("result", ["passed", "failed"])
    async def test_passed_failed_filter_needs_a_percentage(self, db, crowd, result):
        with pytest.raises(ValidationException):
            await _run(db, list(crowd), result=result)

    async def test_default_sort_is_combined_percentage_desc_incomplete_last(self, db, crowd):
        data = await _run(db, list(crowd))

        assert [c["candidate"]["name"] for c in data["candidates"]] == [
            "Anil A",
            "Bina B",
            "Chet C",
            "Dara D",
        ]

    async def test_sort_ascending_keeps_incomplete_last(self, db, crowd):
        data = await _run(db, list(crowd), sort_order="asc")

        assert [c["candidate"]["name"] for c in data["candidates"]] == [
            "Chet C",
            "Bina B",
            "Anil A",
            "Dara D",
        ]

    async def test_sort_by_name(self, db, crowd):
        asc = await _run(db, list(crowd), sort_by="name", sort_order="asc")
        desc = await _run(db, list(crowd), sort_by="name", sort_order="desc")

        assert [c["candidate"]["name"] for c in asc["candidates"]][0] == "Anil A"
        assert [c["candidate"]["name"] for c in desc["candidates"]][0] == "Dara D"

    async def test_search_by_name_and_email(self, db, crowd):
        by_name = await _run(db, list(crowd), search="bina")
        by_email = await _run(db, list(crowd), search="chet.c@")

        assert [c["candidate"]["name"] for c in by_name["candidates"]] == ["Bina B"]
        assert [c["candidate"]["name"] for c in by_email["candidates"]] == ["Chet C"]

    async def test_search_treats_input_literally(self, db, crowd):
        data = await _run(db, list(crowd), search=".*")

        assert data["candidates"] == []

    async def test_pagination(self, db, crowd):
        first = await _run(db, list(crowd), page=1, page_size=3)
        second = await _run(db, list(crowd), page=2, page_size=3)

        assert [c["candidate"]["name"] for c in first["candidates"]] == [
            "Anil A",
            "Bina B",
            "Chet C",
        ]
        assert [c["candidate"]["name"] for c in second["candidates"]] == ["Dara D"]
        assert first["pagination"]["total"] == 4
        assert first["pagination"]["total_pages"] == 2
        assert first["pagination"]["has_next"] is True

    async def test_assessments_are_returned_in_the_requested_order(self, db, crowd):
        a, b = crowd

        data = await _run(db, [b, a])

        assert [x["name"] for x in data["assessments"]] == ["SQL", "Python"]

    async def test_candidates_outside_the_selection_are_not_listed(
        self, db, two, workspace, super_admin
    ):
        a, b = two
        other = await _assessment(db, workspace, super_admin, "Other")
        stranger = await _candidate(db, "Zed", "Z")
        await _sub(db, other["id"], stranger, 10, 10)
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 10, 10)
        await _sub(db, b, cand, 10, 10)

        data = await _run(db, [a, b])

        assert list(_by_name(data)) == ["Asha K"]


# ── the multi-id tenant guard ────────────────────────────────────────────────


class TestVerifyAssessments:
    async def test_returns_oids_in_order_without_duplicates(self, db, workspace, two):
        a, b = two

        oids = await verify_assessments_in_workspace(db, str(workspace["_id"]), [b, a, b])

        assert [str(o) for o in oids] == [b, a]

    async def test_one_foreign_id_fails_everything(self, db, workspace, super_admin, two):
        a, _ = two
        other_ws = (await db.workspaces.insert_one({"name": "Other"})).inserted_id
        foreign = (
            await db.assessments.insert_one(
                {"workspace_id": other_ws, "name": "F", "is_active": True}
            )
        ).inserted_id

        with pytest.raises(NotFoundException):
            await verify_assessments_in_workspace(db, str(workspace["_id"]), [a, str(foreign)])

    async def test_malformed_and_missing_ids_are_404(self, db, workspace, two):
        a, _ = two
        for bad in ("not-an-id", str(ObjectId())):
            with pytest.raises(NotFoundException):
                await verify_assessments_in_workspace(db, str(workspace["_id"]), [a, bad])

    async def test_soft_deleted_assessment_is_404(self, db, workspace, two):
        a, b = two
        await db.assessments.update_one({"_id": ObjectId(b)}, {"$set": {"is_active": False}})

        with pytest.raises(NotFoundException):
            await verify_assessments_in_workspace(db, str(workspace["_id"]), [a, b])


# ── HTTP route ───────────────────────────────────────────────────────────────


def _auth(user):
    token = create_access_token(
        {"sub": str(user["_id"]), "role": user["role"], "email": user["email"]}
    )
    return {"Authorization": f"Bearer {token}"}


async def _user(db, email, role, workspace_ids=None):
    doc = {
        "first_name": "Ad",
        "last_name": "Min",
        "email": email,
        "password_hash": hash_password("Passw0rd!x"),  # pragma: allowlist secret
        "role": role,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": workspace_ids or [],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    doc["_id"] = (await db.users.insert_one(doc)).inserted_id
    return doc


@pytest.fixture
def mdb():
    return AsyncMongoMockClient()["score_filter_db"]


@pytest.fixture
def client(mdb):
    app = create_application()
    app.dependency_overrides[get_db] = lambda: mdb
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
async def world(mdb):
    """Tenant A with two assessments + three candidates; tenant B with one assessment."""
    ws_a = (await mdb.workspaces.insert_one({"name": "A"})).inserted_id
    ws_b = (await mdb.workspaces.insert_one({"name": "B"})).inserted_id
    adm_a = await _user(mdb, "a@x.com", UserRole.ADMIN, [str(ws_a)])
    adm_b = await _user(mdb, "b@x.com", UserRole.ADMIN, [str(ws_b)])
    sup = await _user(mdb, "sup@x.com", UserRole.SUPER_ADMIN)
    cand_user = await _user(mdb, "c@x.com", UserRole.CANDIDATE, [str(ws_a)])

    async def asm(ws, name, active=True):
        return str(
            (
                await mdb.assessments.insert_one(
                    {"workspace_id": ws, "name": name, "is_active": active, "rounds": []}
                )
            ).inserted_id
        )

    a1, a2 = await asm(ws_a, "Python"), await asm(ws_a, "SQL")
    b1 = await asm(ws_b, "Foreign")
    gone = await asm(ws_a, "Deleted", active=False)
    for first, py, sql in (("Anil", 9, 9), ("Bina", 6, 6), ("Chet", 3, 3)):
        cand = await _candidate(mdb, first, "T")
        await _sub(mdb, a1, cand, py, 10)
        await _sub(mdb, a2, cand, sql, 10)
    return {
        "ws_a": str(ws_a),
        "ws_b": str(ws_b),
        "a1": a1,
        "a2": a2,
        "b1": b1,
        "gone": gone,
        "adm_a": adm_a,
        "adm_b": adm_b,
        "sup": sup,
        "cand": cand_user,
    }


def _url(ws, ids, **params):
    query = [("assessment_ids", i) for i in ids] + list(params.items())
    qs = "&".join(f"{k}={v}" for k, v in query)
    return f"/api/workspaces/{ws}/assessments/score-filter?{qs}"


class TestRoute:
    def test_returns_every_declared_field(self, client, world):
        """ApiResponse[ScoreFilterResponse] drops undeclared keys, so assert the real body."""
        r = client.get(
            _url(world["ws_a"], [world["a1"], world["a2"]], pass_percentage=50),
            headers=_auth(world["adm_a"]),
        )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        data = body["data"]
        assert set(data) == set(ScoreFilterResponse.model_fields)
        assert data["pass_percentage"] == 50
        assert data["summary"] == {
            "total": 3,
            "completed": 3,
            "passed": 2,
            "failed": 1,
            "incomplete": 0,
        }
        assert data["pagination"]["total"] == 3
        top = data["candidates"][0]
        assert top["candidate"]["name"] == "Anil T"
        assert top["combined_percentage"] == 90.0
        assert (top["combined_score"], top["combined_total"]) == (18, 20)
        assert top["result"] == "passed"
        assert [b["status"] for b in top["breakdown"]] == ["completed", "completed"]

    def test_result_filter_over_http(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"], world["a2"]], pass_percentage=50, result="failed"),
            headers=_auth(world["adm_a"]),
        )

        names = [c["candidate"]["name"] for c in r.json()["data"]["candidates"]]
        assert names == ["Chet T"]

    def test_super_admin_may_read_any_workspace(self, client, world):
        r = client.get(_url(world["ws_b"], [world["b1"]]), headers=_auth(world["sup"]))

        assert r.status_code == 200

    def test_foreign_workspace_is_404(self, client, world):
        r = client.get(_url(world["ws_b"], [world["b1"]]), headers=_auth(world["adm_a"]))

        assert r.status_code == 404

    def test_one_foreign_assessment_among_own_is_404(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"], world["b1"]]), headers=_auth(world["adm_a"])
        )

        assert r.status_code == 404
        assert "Foreign" not in r.text

    def test_malformed_assessment_id_is_404(self, client, world):
        r = client.get(_url(world["ws_a"], [world["a1"], "nope"]), headers=_auth(world["adm_a"]))

        assert r.status_code == 404

    def test_soft_deleted_assessment_is_404(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"], world["gone"]]), headers=_auth(world["adm_a"])
        )

        assert r.status_code == 404

    def test_candidate_token_is_forbidden(self, client, world):
        r = client.get(_url(world["ws_a"], [world["a1"]]), headers=_auth(world["cand"]))

        assert r.status_code == 403

    def test_requires_authentication(self, client, world):
        assert client.get(_url(world["ws_a"], [world["a1"]])).status_code in (401, 403)

    @pytest.mark.parametrize(
        "params",
        [
            {"pass_percentage": 101},
            {"pass_percentage": -1},
            {"result": "maybe"},
            {"sort_by": "password_hash"},
            {"page": 0},
            {"page_size": 101},
            {"result": "passed"},  # needs pass_percentage
        ],
    )
    def test_bad_query_is_422(self, client, world, params):
        r = client.get(_url(world["ws_a"], [world["a1"]], **params), headers=_auth(world["adm_a"]))

        assert r.status_code == 422

    def test_assessment_ids_required_and_capped(self, client, world):
        h = _auth(world["adm_a"])
        base = f"/api/workspaces/{world['ws_a']}/assessments/score-filter"

        assert client.get(base, headers=h).status_code == 422
        too_many = [str(ObjectId()) for _ in range(11)]
        assert client.get(_url(world["ws_a"], too_many), headers=h).status_code == 422

    def test_fixed_path_is_not_captured_by_the_assessment_id_route(self, client, world):
        """GET /{assessment_id} would 404 on 'score-filter'; this must reach the handler."""
        r = client.get(_url(world["ws_a"], [world["a1"]]), headers=_auth(world["adm_a"]))

        assert r.status_code == 200
        assert r.json()["message"] == "Score filter results retrieved"
