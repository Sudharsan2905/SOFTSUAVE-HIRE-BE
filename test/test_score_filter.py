"""Score Filter: a flat list of submissions across several assessments, judged against one mark."""

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


def _args(**kw):
    return {
        "pass_percentage": None,
        "result": "all",
        "search": None,
        "sort_by": "percentage",
        "sort_order": "desc",
        **kw,
    }


async def _run(db, oids, **kw):
    args = {"page": 1, "page_size": 20, **_args(**kw)}
    return await score_filter_service.get_score_filter(db, [ObjectId(o) for o in oids], **args)


async def _export(db, oids, **kw):
    return await score_filter_service.export_score_filter(
        db, [ObjectId(o) for o in oids], **_args(**kw)
    )


def _pairs(data):
    """(candidate, assessment) of each entry, in order."""
    return [(e["candidate"]["name"], e["assessment"]["name"]) for e in data["entries"]]


def _entry(data, candidate, assessment):
    (match,) = [
        e
        for e in data["entries"]
        if e["candidate"]["name"] == candidate and e["assessment"]["name"] == assessment
    ]
    return match


@pytest.fixture
async def two(db, workspace, super_admin):
    a = await _assessment(db, workspace, super_admin, "Python")
    b = await _assessment(db, workspace, super_admin, "SQL")
    return a["id"], b["id"]


# ── flat entries ─────────────────────────────────────────────────────────────


class TestFlatEntries:
    async def test_one_entry_per_submission_not_grouped_by_candidate(self, db, two):
        a, b = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 8, 10)
        await _sub(db, b, cand, 4, 10)

        data = await _run(db, [a, b])

        assert _pairs(data) == [("Asha K", "Python"), ("Asha K", "SQL")]
        assert data["summary"]["total"] == 2
        assert data["pagination"]["total"] == 2

    async def test_each_entry_carries_its_own_score(self, db, two):
        a, b = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 8, 10)
        await _sub(db, b, cand, 4, 10)

        data = await _run(db, [a, b])

        py, sql = _entry(data, "Asha K", "Python"), _entry(data, "Asha K", "SQL")
        assert (py["score"], py["total"], py["percentage"]) == (8, 10, 80.0)
        assert (sql["score"], sql["total"], sql["percentage"]) == (4, 10, 40.0)
        assert py["assessment"]["id"] == a
        assert py["status"] == "completed"
        assert "breakdown" not in py and "combined_percentage" not in py

    async def test_duplicate_entries_are_kept(self, db, two):
        """Two active submissions for the same candidate and assessment are both listed."""
        a, _ = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 8, 10)
        await _sub(db, a, cand, 3, 10)

        data = await _run(db, [a])

        assert _pairs(data) == [("Asha K", "Python"), ("Asha K", "Python")]
        assert sorted(e["percentage"] for e in data["entries"]) == [30.0, 80.0]
        assert data["summary"]["total"] == 2

    async def test_boundary_is_inclusive(self, db, two):
        a, _ = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 6, 10)

        at_mark = await _run(db, [a], pass_percentage=60)
        above_mark = await _run(db, [a], pass_percentage=60.1)

        assert _entry(at_mark, "Asha K", "Python")["result"] == "passed"
        assert _entry(above_mark, "Asha K", "Python")["result"] == "failed"

    async def test_each_entry_is_judged_on_its_own_percentage(self, db, two):
        a, b = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 9, 10)  # 90
        await _sub(db, b, cand, 2, 10)  # 20

        data = await _run(db, [a, b], pass_percentage=50)

        assert _entry(data, "Asha K", "Python")["result"] == "passed"
        assert _entry(data, "Asha K", "SQL")["result"] == "failed"
        assert data["summary"]["passed"] == 1
        assert data["summary"]["failed"] == 1

    async def test_no_pass_percentage_means_no_result_yet(self, db, two):
        a, _ = two
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 8, 10)

        data = await _run(db, [a])

        assert _entry(data, "Asha K", "Python")["result"] is None
        assert data["pass_percentage"] is None
        assert data["summary"]["passed"] == 0
        assert data["summary"]["failed"] == 0
        assert data["summary"]["completed"] == 1

    async def test_no_stored_pass_mark_is_used(self, db, two):
        """The typed percentage decides, whatever the assessments' own pass mark says."""
        a, _ = two
        await db.assessments.update_many({}, {"$set": {"pass_percentage": 99}})
        cand = await _candidate(db, "Asha", "K")
        await _sub(db, a, cand, 5, 10)

        data = await _run(db, [a], pass_percentage=50)

        assert _entry(data, "Asha K", "Python")["result"] == "passed"


# ── incomplete entries ───────────────────────────────────────────────────────


class TestIncomplete:
    async def test_assessment_not_taken_has_no_entry(self, db, two):
        a, b = two
        cand = await _candidate(db, "Ravi", "S")
        await _sub(db, a, cand, 10, 10)

        data = await _run(db, [a, b], pass_percentage=50)

        assert _pairs(data) == [("Ravi S", "Python")]
        assert data["summary"]["incomplete"] == 0

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

        data = await _run(db, [a, b], pass_percentage=50)

        done = _entry(data, "Ravi S", "Python")
        open_ = _entry(data, "Ravi S", "SQL")
        assert done["result"] == "passed"  # the finished assessment is still judged
        assert open_["result"] == "incomplete"
        assert open_["status"] == expected
        assert (open_["score"], open_["total"], open_["percentage"]) == (None, None, None)

    async def test_round_without_a_score_is_not_final(self, db, two):
        a, _ = two
        cand = await _candidate(db, "Ravi", "S")
        await _sub(
            db, a, cand, 5, 10, rounds_data=[{"score": 5, "questions": [{}]}, {"questions": [{}]}]
        )

        data = await _run(db, [a])

        assert _entry(data, "Ravi S", "Python")["result"] == "incomplete"

    async def test_soft_deleted_submission_has_no_entry(self, db, two):
        a, b = two
        cand = await _candidate(db, "Ravi", "S")
        await _sub(db, a, cand, 10, 10)
        await _sub(db, b, cand, 10, 10, is_active=False)

        data = await _run(db, [a, b])

        assert _pairs(data) == [("Ravi S", "Python")]

    async def test_reaccess_reset_submission_is_incomplete(self, db, two):
        a, _ = two
        cand = await _candidate(db, "Ravi", "S")
        # What re-access leaves behind: pending, score reset, fresh unscored rounds.
        await _sub(
            db,
            a,
            cand,
            0,
            10,
            status="pending",
            percentage=0.0,
            rounds_data=[{"questions": [{}] * 10}],
        )

        data = await _run(db, [a], pass_percentage=50)

        assert _entry(data, "Ravi S", "Python")["result"] == "incomplete"


# ── filtering, search, sorting, paging, summary ──────────────────────────────


@pytest.fixture
async def crowd(db, two):
    """Seven entries: Anil 90/80, Bina 60/40, Chet 30 (Python only), Dara 100 + SQL open."""
    a, b = two
    for first, last, py, sql in (("Anil", "A", 9, 8), ("Bina", "B", 6, 4)):
        cand = await _candidate(db, first, last)
        await _sub(db, a, cand, py, 10)
        await _sub(db, b, cand, sql, 10)
    await _sub(db, a, await _candidate(db, "Chet", "C"), 3, 10)
    dara = await _candidate(db, "Dara", "D")
    await _sub(db, a, dara, 10, 10)
    await _sub(db, b, dara, 10, 10, status="in_progress")
    return a, b


class TestListing:
    async def test_summary_counts_ignore_the_result_filter(self, db, crowd):
        data = await _run(db, list(crowd), pass_percentage=50, result="passed")

        assert data["summary"] == {
            "total": 7,
            "completed": 6,
            "passed": 4,
            "failed": 2,
            "incomplete": 1,
        }
        assert data["pagination"]["total"] == 4

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            (
                "all",
                [
                    ("Dara D", "Python"),
                    ("Anil A", "Python"),
                    ("Anil A", "SQL"),
                    ("Bina B", "Python"),
                    ("Bina B", "SQL"),
                    ("Chet C", "Python"),
                    ("Dara D", "SQL"),
                ],
            ),
            (
                "passed",
                [
                    ("Dara D", "Python"),
                    ("Anil A", "Python"),
                    ("Anil A", "SQL"),
                    ("Bina B", "Python"),
                ],
            ),
            ("failed", [("Bina B", "SQL"), ("Chet C", "Python")]),
            ("incomplete", [("Dara D", "SQL")]),
        ],
    )
    async def test_result_filter(self, db, crowd, result, expected):
        data = await _run(db, list(crowd), pass_percentage=50, result=result)

        assert sorted(_pairs(data)) == sorted(expected)

    async def test_changing_the_percentage_changes_results_without_storing_anything(
        self, db, crowd
    ):
        before = await db.assessment_submissions.find().to_list(None)

        low = await _run(db, list(crowd), pass_percentage=20)
        high = await _run(db, list(crowd), pass_percentage=95)

        assert low["summary"]["passed"] == 6
        assert high["summary"]["passed"] == 1  # only Dara's 100
        assert await db.assessment_submissions.find().to_list(None) == before

    @pytest.mark.parametrize("result", ["passed", "failed"])
    async def test_passed_failed_filter_needs_a_percentage(self, db, crowd, result):
        with pytest.raises(ValidationException):
            await _run(db, list(crowd), result=result)

    async def test_default_sort_is_percentage_desc_unscored_last(self, db, crowd):
        data = await _run(db, list(crowd))

        assert [e["percentage"] for e in data["entries"]] == [
            100.0,
            90.0,
            80.0,
            60.0,
            40.0,
            30.0,
            None,
        ]

    async def test_sort_ascending_keeps_unscored_last(self, db, crowd):
        data = await _run(db, list(crowd), sort_order="asc")

        assert [e["percentage"] for e in data["entries"]] == [
            30.0,
            40.0,
            60.0,
            80.0,
            90.0,
            100.0,
            None,
        ]

    async def test_sort_by_name_keeps_a_candidates_entries_together(self, db, crowd):
        asc = await _run(db, list(crowd), sort_by="name", sort_order="asc")
        desc = await _run(db, list(crowd), sort_by="name", sort_order="desc")

        assert _pairs(asc) == [
            ("Anil A", "Python"),
            ("Anil A", "SQL"),
            ("Bina B", "Python"),
            ("Bina B", "SQL"),
            ("Chet C", "Python"),
            ("Dara D", "Python"),
            ("Dara D", "SQL"),
        ]
        assert _pairs(desc)[:2] == [("Dara D", "Python"), ("Dara D", "SQL")]
        assert _pairs(desc)[-2:] == [("Anil A", "Python"), ("Anil A", "SQL")]

    async def test_ties_follow_the_requested_assessment_order(self, db, crowd):
        a, b = crowd

        data = await _run(db, [b, a], sort_by="name", sort_order="asc")

        assert _pairs(data)[:2] == [("Anil A", "SQL"), ("Anil A", "Python")]

    async def test_search_by_name_and_email(self, db, crowd):
        by_name = await _run(db, list(crowd), search="bina")
        by_email = await _run(db, list(crowd), search="chet.c@")

        assert {c for c, _ in _pairs(by_name)} == {"Bina B"}
        assert len(by_name["entries"]) == 2
        assert _pairs(by_email) == [("Chet C", "Python")]

    async def test_search_treats_input_literally(self, db, crowd):
        data = await _run(db, list(crowd), search=".*")

        assert data["entries"] == []

    async def test_pagination_counts_entries(self, db, crowd):
        pages = [await _run(db, list(crowd), page=p, page_size=3) for p in (1, 2, 3)]

        assert [len(p["entries"]) for p in pages] == [3, 3, 1]
        assert pages[0]["pagination"]["total"] == 7
        assert pages[0]["pagination"]["total_pages"] == 3
        assert pages[0]["pagination"]["has_next"] is True
        assert pages[2]["pagination"]["has_next"] is False
        assert pages[2]["entries"][0]["status"] == "in_progress"

    async def test_assessments_are_returned_in_the_requested_order(self, db, crowd):
        a, b = crowd

        data = await _run(db, [b, a])

        assert [x["name"] for x in data["assessments"]] == ["SQL", "Python"]

    async def test_submissions_outside_the_selection_are_not_listed(
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

        assert {c for c, _ in _pairs(data)} == {"Asha K"}


# ── export ───────────────────────────────────────────────────────────────────


class TestExport:
    async def test_returns_every_entry_unpaged_as_flat_rows(self, db, crowd):
        rows = await _export(db, list(crowd), pass_percentage=50)

        assert len(rows) == 7  # more than one default page would hold at page_size=3
        assert set(rows[0]) == {
            "name",
            "email",
            "assessment",
            "status",
            "score",
            "total",
            "percentage",
            "result",
        }
        assert rows[0] == {
            "name": "Dara D",
            "email": "dara.d@example.com",
            "assessment": "Python",
            "status": "completed",
            "score": 10,
            "total": 10,
            "percentage": 100.0,
            "result": "passed",
        }

    async def test_keeps_a_candidate_once_per_assessment(self, db, crowd):
        rows = await _export(db, list(crowd), search="anil")

        assert [(r["name"], r["assessment"]) for r in rows] == [
            ("Anil A", "Python"),
            ("Anil A", "SQL"),
        ]

    async def test_applies_the_same_filters_as_the_list(self, db, crowd):
        kwargs = {"pass_percentage": 50, "result": "failed", "sort_order": "asc"}

        rows = await _export(db, list(crowd), **kwargs)
        data = await _run(db, list(crowd), **kwargs)

        assert [(r["name"], r["assessment"]) for r in rows] == _pairs(data)
        assert [(r["name"], r["assessment"]) for r in rows] == [
            ("Chet C", "Python"),
            ("Bina B", "SQL"),
        ]

    async def test_unfinished_entry_has_no_score_and_is_incomplete(self, db, crowd):
        rows = await _export(db, list(crowd), result="incomplete")

        assert rows == [
            {
                "name": "Dara D",
                "email": "dara.d@example.com",
                "assessment": "SQL",
                "status": "in_progress",
                "score": None,
                "total": None,
                "percentage": None,
                "result": "incomplete",
            }
        ]

    @pytest.mark.parametrize("result", ["passed", "failed"])
    async def test_passed_failed_filter_needs_a_percentage(self, db, crowd, result):
        with pytest.raises(ValidationException):
            await _export(db, list(crowd), result=result)


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


def _url(ws, ids, export=False, **params):
    query = [("assessment_ids", i) for i in ids] + list(params.items())
    qs = "&".join(f"{k}={v}" for k, v in query)
    suffix = "/export" if export else ""
    return f"/api/workspaces/{ws}/assessments/score-filter{suffix}?{qs}"


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
            "total": 6,
            "completed": 6,
            "passed": 4,
            "failed": 2,
            "incomplete": 0,
        }
        assert data["pagination"]["total"] == 6
        assert len(data["entries"]) == 6
        top = data["entries"][0]
        assert top["candidate"]["name"] == "Anil T"
        assert top["assessment"] == {"id": world["a1"], "name": "Python"}
        assert top["percentage"] == 90.0
        assert (top["score"], top["total"]) == (9, 10)
        assert top["status"] == "completed"
        assert top["result"] == "passed"

    def test_lists_a_candidate_once_per_assessment(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"], world["a2"]], search="Anil"),
            headers=_auth(world["adm_a"]),
        )

        entries = r.json()["data"]["entries"]
        assert [(e["candidate"]["name"], e["assessment"]["name"]) for e in entries] == [
            ("Anil T", "Python"),
            ("Anil T", "SQL"),
        ]

    def test_result_filter_over_http(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"], world["a2"]], pass_percentage=50, result="failed"),
            headers=_auth(world["adm_a"]),
        )

        names = [e["candidate"]["name"] for e in r.json()["data"]["entries"]]
        assert names == ["Chet T", "Chet T"]

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
            {"sort_by": "combined_percentage"},  # the old, grouped sort key is gone
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


class TestExportRoute:
    def test_returns_all_rows_flat_in_the_standard_envelope(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"], world["a2"]], export=True, pass_percentage=50),
            headers=_auth(world["adm_a"]),
        )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["message"] == "Export data retrieved"
        rows = body["data"]
        assert len(rows) == 6  # a candidate per assessment, nothing grouped or de-duplicated
        assert rows[0] == {
            "name": "Anil T",
            "email": "anil.t@example.com",
            "assessment": "Python",
            "status": "completed",
            "score": 9,
            "total": 10,
            "percentage": 90.0,
            "result": "passed",
        }

    def test_filters_apply(self, client, world):
        r = client.get(
            _url(
                world["ws_a"],
                [world["a1"], world["a2"]],
                export=True,
                pass_percentage=50,
                result="failed",
            ),
            headers=_auth(world["adm_a"]),
        )

        assert [row["name"] for row in r.json()["data"]] == ["Chet T", "Chet T"]

    def test_fixed_path_is_not_captured_by_the_assessment_id_route(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"]], export=True), headers=_auth(world["adm_a"])
        )

        assert r.status_code == 200
        assert r.json()["message"] == "Export data retrieved"

    def test_one_foreign_assessment_among_own_is_404(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"], world["b1"]], export=True),
            headers=_auth(world["adm_a"]),
        )

        assert r.status_code == 404

    def test_candidate_token_is_forbidden(self, client, world):
        r = client.get(
            _url(world["ws_a"], [world["a1"]], export=True), headers=_auth(world["cand"])
        )

        assert r.status_code == 403

    def test_requires_authentication(self, client, world):
        r = client.get(_url(world["ws_a"], [world["a1"]], export=True))

        assert r.status_code in (401, 403)

    @pytest.mark.parametrize(
        "params",
        [
            {"pass_percentage": 101},
            {"result": "maybe"},
            {"sort_by": "password_hash"},
            {"result": "failed"},
        ],
    )
    def test_bad_query_is_422(self, client, world, params):
        r = client.get(
            _url(world["ws_a"], [world["a1"]], export=True, **params),
            headers=_auth(world["adm_a"]),
        )

        assert r.status_code == 422
