"""Security regression tests: tenant isolation, uploads, image keys, auth, error leakage."""

import asyncio
import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.common import uploads
from app.common.constants.app_constants import SubmissionStatus, UserRole
from app.common.exceptions import (
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.common.utils import email_query, hash_token, normalize_email, utcnow
from app.common.validators import is_valid_question_image_key
from app.components.auth import auth_service
from app.components.auth.auth_service import create_access_token, hash_password
from app.components.candidate import candidate_service
from app.components.users import user_service
from app.core.dependencies import get_db
from app.factory import create_application

PW = "Passw0rd!x"  # NOSONAR - test credential


def _tok(user: dict) -> dict:
    t = create_access_token({"sub": str(user["_id"]), "role": user["role"], "email": user["email"]})
    return {"Authorization": f"Bearer {t}"}


async def _user(db, email, role, workspace_ids=None, **extra):
    doc = {
        "first_name": "F",
        "last_name": "L",
        "email": email,
        "password_hash": hash_password(PW),
        "role": role,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": workspace_ids or [],
        "created_at": utcnow(),
        "updated_at": utcnow(),
        **extra,
    }
    doc["_id"] = (await db.users.insert_one(doc)).inserted_id
    return doc


@pytest.fixture
def mdb():
    return AsyncMongoMockClient()["sec_db"]


@pytest.fixture
def client(mdb):
    app = create_application()
    app.dependency_overrides[get_db] = lambda: mdb
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
async def world(mdb):
    """Two tenants (A, B), one admin each, a super admin, one assessment+submission each."""
    ws_a = (await mdb.workspaces.insert_one({"name": "A"})).inserted_id
    ws_b = (await mdb.workspaces.insert_one({"name": "B"})).inserted_id
    sup = await _user(mdb, "sup@x.com", UserRole.SUPER_ADMIN)
    adm_a = await _user(mdb, "a@x.com", UserRole.ADMIN, [str(ws_a)])
    adm_b = await _user(mdb, "b@x.com", UserRole.ADMIN, [str(ws_b)])
    cand_a = await _user(mdb, "ca@x.com", UserRole.CANDIDATE)
    cand_b = await _user(mdb, "cb@x.com", UserRole.CANDIDATE)
    out = {"ws_a": ws_a, "ws_b": ws_b, "sup": sup, "adm_a": adm_a, "adm_b": adm_b}
    for key, ws, cand in (("a", ws_a, cand_a), ("b", ws_b, cand_b)):
        asm = (
            await mdb.assessments.insert_one(
                {
                    "workspace_id": ws,
                    "name": f"Asm {key}",
                    "is_active": True,
                    "rounds": [],
                    "created_at": utcnow(),
                }
            )
        ).inserted_id
        sub = (
            await mdb.assessment_submissions.insert_one(
                {
                    "assessment_id": asm,
                    "candidate_id": cand["_id"],
                    "status": SubmissionStatus.IN_PROGRESS,
                    "current_round": 1,
                    "rounds_data": [],
                    "workspace_id": ws,
                    "started_at": utcnow(),
                    "created_at": utcnow(),
                }
            )
        ).inserted_id
        share = (
            await mdb.assessment_shares.insert_one(
                {
                    "assessment_id": asm,
                    "workspace_id": ws,
                    "share_link": f"link-{key}",
                    "is_active": True,
                }
            )
        ).inserted_id
        out.update(
            {f"asm_{key}": asm, f"sub_{key}": sub, f"share_{key}": share, f"cand_{key}": cand}
        )
    return out


def _p(ws, asm, tail=""):
    return f"/api/workspaces/{ws}/assessments/{asm}{tail}"


# ═══════════════════════════ 1. tenant isolation ═══════════════════════════


class TestTenantIsolation:
    def test_member_can_read_own_assessment(self, client, world):
        r = client.get(_p(world["ws_a"], world["asm_a"]), headers=_tok(world["adm_a"]))
        assert r.status_code != 404  # the guard lets a member through (body shape is irrelevant)

    def test_foreign_workspace_is_404(self, client, world):
        for path in (
            f"/api/workspaces/{world['ws_b']}/assessments",
            f"/api/workspaces/{world['ws_b']}/assessments/stats",
            _p(world["ws_b"], world["asm_b"]),
            _p(world["ws_b"], world["asm_b"], "/submissions"),
            _p(world["ws_b"], world["asm_b"], "/submissions/export"),
            _p(world["ws_b"], world["asm_b"], "/submissions/stats"),
            _p(world["ws_b"], world["asm_b"], f"/submissions/{world['sub_b']}"),
            _p(world["ws_b"], world["asm_b"], "/shares"),
        ):
            r = client.get(path, headers=_tok(world["adm_a"]))
            assert r.status_code == 404, path

    def test_own_workspace_with_foreign_assessment_is_404(self, client, world):
        r = client.get(_p(world["ws_a"], world["asm_b"]), headers=_tok(world["adm_a"]))
        assert r.status_code == 404

    def test_own_assessment_with_foreign_submission_is_404(self, client, world):
        h = _tok(world["adm_a"])
        base = _p(world["ws_a"], world["asm_a"], f"/submissions/{world['sub_b']}")
        assert client.get(base, headers=h).status_code == 404
        assert client.delete(base, headers=h).status_code == 404
        for action, body in (
            ("terminate", {"reason": "x"}),
            ("complete", None),
            ("resume", None),
            ("reaccess", {"reason": "x" * 10, "reason_category": "technical"}),
        ):
            r = client.post(f"{base}/{action}", headers=h, json=body)
            assert r.status_code == 404, action

    async def test_foreign_submission_state_untouched(self, client, world, mdb):
        client.post(
            _p(
                world["ws_b"],
                world["asm_b"],
                f"/submissions/{world['sub_b']}/terminate",
            ),
            headers=_tok(world["adm_a"]),
            json={"reason": "attack"},
        )
        sub = await mdb.assessment_submissions.find_one({"_id": world["sub_b"]})
        assert sub["status"] == SubmissionStatus.IN_PROGRESS

    def test_candidate_routes_are_guarded(self, client, world):
        h = _tok(world["adm_a"])
        cid = world["cand_b"]["_id"]
        for tail in (f"/candidates/{cid}/submission", f"/candidates/{cid}/versions"):
            assert client.get(_p(world["ws_b"], world["asm_b"], tail), headers=h).status_code == 404
            # right workspace/assessment of A, but nothing of B's leaks through
            r = client.get(_p(world["ws_a"], world["asm_a"], tail), headers=h)
            assert r.status_code in (404, 422)

    def test_share_update_and_delete_cannot_cross_tenants(self, client, world):
        h = _tok(world["adm_a"])
        # A's own path with B's share id
        p = _p(world["ws_a"], world["asm_a"], f"/shares/{world['share_b']}")
        assert client.delete(p, headers=h).status_code == 404
        assert client.put(p, headers=h, json={"is_active": False}).status_code == 404
        # B's path altogether
        p2 = _p(world["ws_b"], world["asm_b"], f"/shares/{world['share_b']}")
        assert client.delete(p2, headers=h).status_code == 404

    async def test_foreign_share_not_revoked(self, client, world, mdb):
        client.delete(
            _p(world["ws_a"], world["asm_a"], f"/shares/{world['share_b']}"),
            headers=_tok(world["adm_a"]),
        )
        share = await mdb.assessment_shares.find_one({"_id": world["share_b"]})
        assert share["is_active"] is True

    def test_super_admin_passes_everywhere(self, client, world):
        h = _tok(world["sup"])
        assert client.get(_p(world["ws_b"], world["asm_b"]), headers=h).status_code != 404
        assert (
            client.get(
                _p(world["ws_b"], world["asm_b"], f"/submissions/{world['sub_b']}"), headers=h
            ).status_code
            != 404
        )

    def test_malformed_ids_are_404_not_500(self, client, world):
        h = _tok(world["adm_a"])
        for path in (
            "/api/workspaces/not-an-id/assessments",
            _p(world["ws_a"], "zzz"),
            _p(world["ws_a"], world["asm_a"], "/submissions/zzz"),
        ):
            assert client.get(path, headers=h).status_code == 404

    def test_livekit_admin_token_requires_membership(self, client, world):
        url = "/api/live-interviews/livekit-token"
        h = _tok(world["adm_a"])
        assert (
            client.post(url, headers=h, json={"workspace_id": str(world["ws_b"])}).status_code
            == 404
        )
        assert (
            client.post(url, headers=h, json={"workspace_id": str(world["ws_a"])}).status_code
            == 200
        )
        hs = _tok(world["sup"])
        assert (
            client.post(url, headers=hs, json={"workspace_id": str(world["ws_b"])}).status_code
            == 200
        )


# ═══════════════════════════ 2. uploads ═══════════════════════════


class _FakeUpload:
    def __init__(self, data: bytes):
        self._s = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._s.read(n)


JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class TestUploads:
    @pytest.mark.parametrize(
        ("data", "mime"),
        [
            (JPEG, uploads.MIME_JPEG),
            (PNG, uploads.MIME_PNG),
            (b"GIF89a" + b"0" * 20, uploads.MIME_GIF),
            (b"RIFF\x00\x00\x00\x00WEBPVP8 ", uploads.MIME_WEBP),
            (b"\x1a\x45\xdf\xa3" + b"0" * 20, uploads.MIME_WEBM),
            (b"\x00\x00\x00\x18ftypmp42", uploads.MIME_MP4),
            (b"OggS\x00\x02" + b"0" * 20, uploads.MIME_OGG),
            (b"ID3\x04" + b"0" * 20, uploads.MIME_MP3),
            (b"%PDF-1.4", None),
            (b"<html>", None),
            (b"MZ\x90\x00", None),
        ],
    )
    def test_sniff(self, data, mime):
        assert uploads.sniff_mime(data) == mime

    async def test_read_capped_aborts_early(self):
        class Endless:
            reads = 0

            async def read(self, n=-1):
                Endless.reads += 1
                return b"x" * n

        with pytest.raises(uploads.PayloadTooLargeException):
            await uploads.read_capped(Endless(), 1024 * 1024)
        assert Endless.reads < 10  # stopped right after crossing the cap, did not slurp more

    async def test_read_validated_rejects_lying_content_type(self):
        with pytest.raises(ValidationException):
            await uploads.read_validated(
                _FakeUpload(b"%PDF-1.4 junk"), {uploads.MIME_JPEG}, 1024, "Screenshot"
            )

    async def test_read_validated_rejects_empty(self):
        with pytest.raises(ValidationException):
            await uploads.read_validated(_FakeUpload(b""), {uploads.MIME_JPEG}, 1024, "x")

    def test_xlsx_zip_bomb_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("big.xml", b"0" * (uploads.MAX_XLSX_UNCOMPRESSED_BYTES + 1))
        with pytest.raises(ValidationException, match="too large"):
            uploads.check_xlsx_archive(buf.getvalue())

    def test_xlsx_too_many_entries_and_non_zip_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for i in range(uploads.MAX_XLSX_ENTRIES + 1):
                z.writestr(f"f{i}", b"x")
        with pytest.raises(ValidationException, match="too many"):
            uploads.check_xlsx_archive(buf.getvalue())
        with pytest.raises(ValidationException):
            uploads.check_xlsx_archive(b"not a zip at all")

    async def test_excel_import_runs_the_archive_guard(self, mdb):
        from app.components.question_bank import question_service

        with pytest.raises(ValidationException):
            await question_service.process_excel_import(
                mdb, str(ObjectId()), b"definitely not xlsx", str(ObjectId()), {}
            )

    def test_body_limit_rejects_oversized_declared_length(self, client, world):
        h = _tok(world["cand_a"])
        r = client.post(
            f"/api/candidate/submission/{world['sub_a']}/screenshot",
            headers={**h, "Content-Length": str(50 * 1024 * 1024)},
            content=b"x",
        )
        assert r.status_code == 413

    def test_body_limit_rejects_big_json(self, client, world):
        r = client.post(
            "/api/auth/login",
            content=b"{" + b"x" * (3 * 1024 * 1024),
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413

    def test_screenshot_with_lying_content_type_is_rejected(self, client, world):
        r = client.post(
            f"/api/candidate/submission/{world['sub_a']}/screenshot",
            headers=_tok(world["cand_a"]),
            files={"file": ("a.png", io.BytesIO(b"%PDF-1.4 evil"), "image/png")},
        )
        assert r.status_code == 422

    def test_screenshot_real_jpeg_is_accepted(self, client, world):
        with patch.object(candidate_service.s3_service, "upload", AsyncMock()):
            r = client.post(
                f"/api/candidate/submission/{world['sub_a']}/screenshot",
                headers=_tok(world["cand_a"]),
                files={"file": ("a.png", io.BytesIO(JPEG), "application/octet-stream")},
            )
        assert r.status_code == 200

    def test_media_with_lying_types_rejected(self, client, world):
        r = client.post(
            f"/api/candidate/submission/{world['sub_a']}/malpractice/0/media",
            headers=_tok(world["cand_a"]),
            files={"video_chunk": ("v.webm", io.BytesIO(b"MZ\x90 not video"), "video/webm")},
        )
        assert r.status_code == 422

    async def test_screenshot_count_is_capped(self, mdb, world):
        await mdb.assessment_submissions.update_one(
            {"_id": world["sub_a"]},
            {
                "$set": {
                    "screenshots": [{"s3_key": "k"}] * candidate_service.MAX_SCREENSHOTS_PER_SESSION
                }
            },
        )
        upload = AsyncMock()
        with patch.object(candidate_service.s3_service, "upload", upload):
            await candidate_service.save_screenshot(
                mdb, str(world["sub_a"]), str(world["cand_a"]["_id"]), JPEG, "image/jpeg"
            )
        upload.assert_not_called()  # session full: dropped before touching S3
        sub = await mdb.assessment_submissions.find_one({"_id": world["sub_a"]})
        assert len(sub["screenshots"]) == candidate_service.MAX_SCREENSHOTS_PER_SESSION

    async def test_question_image_upload_sniffs_content(self, mdb):
        from app.components.question_bank import question_service

        cat = (await mdb.question_categories.insert_one({"name": "c"})).inserted_id
        with pytest.raises(ValidationException):
            await question_service.upload_question_image(
                mdb,
                str(cat),
                type("F", (), {"read": _FakeUpload(b"%PDF").read, "content_type": "image/png"})(),
            )


# ═══════════════════════════ 3. image_key ═══════════════════════════


class TestImageKey:
    @pytest.mark.parametrize(
        "key",
        [
            "question-images/cat1/a.jpg",
            "question-images/64b/0f1e-22.png",
        ],
    )
    def test_valid(self, key):
        assert is_valid_question_image_key(key)

    @pytest.mark.parametrize(
        "key",
        [
            "evidence/sub/round_1/tab_switch/screen_1.jpg",
            "screenshots/sub/round_1/x.jpg",
            "/question-images/a.jpg",
            "question-images/../evidence/x",
            "question-images//x",
            "question-images/a\\b",
            "question-images/a b",
            "../question-images/a",
            "",
            None,
            123,
        ],
    )
    def test_invalid(self, key):
        assert not is_valid_question_image_key(key)

    def test_schema_rejects_foreign_prefix_but_allows_clear(self):
        from pydantic import ValidationError

        from app.components.question_bank.question_schemas import (
            CreateQuestionRequest,
            QuestionOption,
            UpdateQuestionRequest,
        )

        with pytest.raises(ValidationError):
            UpdateQuestionRequest(image_key="evidence/other-tenant/clip.webm")
        with pytest.raises(ValidationError):
            QuestionOption(id="a", image_key="screenshots/x.jpg")
        with pytest.raises(ValidationError):
            CreateQuestionRequest(
                question_text="Some question",
                question_type="essay",
                complexity="low",
                image_key="../../etc/passwd",
            )
        assert UpdateQuestionRequest(image_key="").image_key == ""  # "" = clear the image
        assert UpdateQuestionRequest(image_key="question-images/c/a.jpg").image_key

    async def test_resolvers_never_presign_foreign_keys(self):
        from app.components.assessment import assessment_service
        from app.components.question_bank import question_service
        from app.components.version_history import version_service

        presign = AsyncMock(return_value="https://signed")
        with patch("app.components.storage.s3_service.get_presigned_url", presign):
            for fn in (
                question_service._resolve_image_url,
                assessment_service._resolve_image_url,
                candidate_service._resolve_question_image_url,
                version_service._resolve_question_image,
            ):
                assert await fn("evidence/victim/clip.webm") is None
            assert await question_service._resolve_image_url("question-images/c/a.jpg")
        presign.assert_awaited_once_with("question-images/c/a.jpg")


# ═══════════════════════════ 4. auth ═══════════════════════════


class TestEmailNormalisation:
    def test_helpers(self):
        assert normalize_email("  Foo@X.COM ") == "foo@x.com"
        assert set(email_query("Foo@X.com")["$in"]) == {"foo@x.com", "Foo@X.com"}

    async def test_register_login_case_insensitive_and_conflict(self, mdb):
        data = {
            "first_name": "Ab",
            "last_name": "C",
            "email": "Mixed@Case.COM",
            "phone": "9876543210",
            "password": PW,
            "gender": "male",
        }
        await auth_service.register_candidate(mdb, dict(data))
        stored = await mdb.users.find_one({})
        assert stored["email"] == "mixed@case.com"
        res = await auth_service.candidate_login(mdb, "MIXED@case.com", PW)
        assert res.access_token
        with pytest.raises(Exception, match="(?i)email|registered|exist"):
            await auth_service.register_candidate(mdb, {**data, "email": "mixed@CASE.com"})

    async def test_legacy_mixed_case_account_can_log_in_with_its_stored_casing(self, mdb):
        # Stored un-normalised before this change. Until scripts/normalize_emails.py is run
        # the exact casing keeps working; afterwards any casing does.
        await _user(mdb, "Legacy@Mail.com", UserRole.CANDIDATE)
        assert (await auth_service.candidate_login(mdb, "Legacy@Mail.com", PW)).access_token
        await mdb.users.update_one({}, {"$set": {"email": "legacy@mail.com"}})  # migration
        assert (await auth_service.candidate_login(mdb, "LEGACY@mail.com", PW)).access_token

    async def test_update_me_email_is_normalised_and_unverified(self, mdb):
        u = await _user(mdb, "me@x.com", UserRole.CANDIDATE, email_verified=True)
        out = await user_service.update_me(mdb, str(u["_id"]), {"email": " NEW@X.com"})
        assert out["email"] == "new@x.com" and out["email_verified"] is False

    async def test_admin_cannot_register_duplicate_by_case(self, mdb):
        await _user(mdb, "dup@x.com", UserRole.CANDIDATE)
        with pytest.raises(Exception, match="(?i)email|exist|registered"):
            await user_service.create_admin_user(
                mdb,
                {
                    "first_name": "A",
                    "email": "DUP@x.com",
                    "password": PW,
                    "role": UserRole.SUPER_ADMIN,
                },
            )


class TestPasswordless:
    async def test_login_for_google_only_account_is_401_not_500(self, mdb):
        await _user(mdb, "g@x.com", UserRole.CANDIDATE, password_hash=None)
        with pytest.raises(UnauthorizedException):
            await auth_service.candidate_login(mdb, "g@x.com", "whatever1!")

    def test_login_route_returns_401(self, client, mdb):
        asyncio.run(_user(mdb, "g2@x.com", UserRole.CANDIDATE, password_hash=None))
        r = client.post("/api/auth/login", json={"email": "g2@x.com", "password": "Whatever1!"})
        assert r.status_code == 401

    def test_malformed_hash_is_just_false(self):
        assert auth_service.verify_password("x", "not-a-bcrypt-hash") is False
        assert auth_service.verify_password("x", None) is False


class TestRefreshTokens:
    async def _login(self, mdb, email="r@x.com"):
        await _user(mdb, email, UserRole.CANDIDATE)
        return await auth_service.candidate_login(mdb, email, PW)

    async def test_rotation_old_token_is_dead(self, mdb):
        t = await self._login(mdb)
        r1 = await auth_service.refresh_access_token(mdb, t.refresh_token)
        assert r1.refresh_token and r1.refresh_token != t.refresh_token
        with pytest.raises(UnauthorizedException):
            await auth_service.refresh_access_token(mdb, t.refresh_token)  # replay
        r2 = await auth_service.refresh_access_token(mdb, r1.refresh_token)
        assert r2.access_token

    async def test_concurrent_use_only_one_wins(self, mdb):
        t = await self._login(mdb)
        results = await asyncio.gather(
            *(auth_service.refresh_access_token(mdb, t.refresh_token) for _ in range(5)),
            return_exceptions=True,
        )
        ok = [r for r in results if not isinstance(r, Exception)]
        assert len(ok) == 1

    async def test_expired_token_rejected(self, mdb):
        t = await self._login(mdb)
        await mdb.refresh_tokens.update_many(
            {}, {"$set": {"expires_at": utcnow().replace(year=2001)}}
        )
        with pytest.raises(UnauthorizedException):
            await auth_service.refresh_access_token(mdb, t.refresh_token)

    async def test_deactivated_user_cannot_refresh(self, mdb):
        t = await self._login(mdb)
        await mdb.users.update_one({}, {"$set": {"is_active": False}})
        with pytest.raises(UnauthorizedException):
            await auth_service.refresh_access_token(mdb, t.refresh_token)
        assert await mdb.refresh_tokens.count_documents({}) == 0

    async def test_logout_other_users_token_forbidden(self, mdb):
        a = await self._login(mdb, "a1@x.com")
        b = await self._login(mdb, "b1@x.com")
        a_user = await mdb.users.find_one({"email": "a1@x.com"})
        with pytest.raises(ForbiddenException):
            await auth_service.logout(mdb, b.refresh_token, caller_id=str(a_user["_id"]))
        assert await mdb.refresh_tokens.find_one({"token_hash": hash_token(b.refresh_token)})
        await auth_service.logout(mdb, a.refresh_token, caller_id=str(a_user["_id"]))
        assert not await mdb.refresh_tokens.find_one({"token_hash": hash_token(a.refresh_token)})

    async def test_logout_without_access_token_still_works_with_valid_refresh(self, mdb):
        t = await self._login(mdb)
        await auth_service.logout(mdb, t.refresh_token)
        assert await mdb.refresh_tokens.count_documents({}) == 0


class TestRevocation:
    async def _with_token(self, mdb, role=UserRole.CANDIDATE, email="v@x.com"):
        u = await _user(mdb, email, role, workspace_ids=[] if role == UserRole.CANDIDATE else [])
        await auth_service._issue_tokens(mdb, u)
        return u

    async def test_password_change_via_update_me(self, mdb):
        u = await self._with_token(mdb)
        await user_service.update_me(
            mdb, str(u["_id"]), {"password": "NewPassw0rd!", "current_password": PW}
        )
        assert await mdb.refresh_tokens.count_documents({"user_id": u["_id"]}) == 0

    async def test_admin_reset_role_change_and_deactivation(self, mdb):
        # mongomock has no arrayFilters (used to sync the role into workspace members)
        with patch("mongomock.collection.Collection.update_many", MagicMock()):
            for change in (
                {"password": "NewPassw0rd!"},
                {"role": UserRole.SUPER_ADMIN},
                {"is_active": False},
            ):
                u = await self._with_token(mdb, UserRole.ADMIN, f"{list(change)[0]}@x.com")
                await user_service.update_user(mdb, str(u["_id"]), dict(change))
                assert await mdb.refresh_tokens.count_documents({"user_id": u["_id"]}) == 0, change

    async def test_harmless_update_keeps_sessions(self, mdb):
        u = await self._with_token(mdb, UserRole.ADMIN, "keep@x.com")
        await user_service.update_user(mdb, str(u["_id"]), {"first_name": "Renamed"})
        assert await mdb.refresh_tokens.count_documents({"user_id": u["_id"]}) == 1

    async def test_candidate_delete_and_deactivate(self, mdb):
        u = await self._with_token(mdb, email="d1@x.com")
        await user_service.delete_candidate(mdb, str(u["_id"]))
        assert await mdb.refresh_tokens.count_documents({"user_id": u["_id"]}) == 0
        u2 = await self._with_token(mdb, email="d2@x.com")
        await user_service.update_candidate(mdb, str(u2["_id"]), {"is_active": False})
        assert await mdb.refresh_tokens.count_documents({"user_id": u2["_id"]}) == 0


class TestGoogle:
    def _reg(self, email="new@g.com", sub="google-sub-1"):
        return auth_service._create_google_registration_token(email, sub)

    def _data(self, email="new@g.com", google_id=None):
        return {
            "first_name": "Gg",
            "last_name": "H",
            "email": email,
            "phone": "9876543210",
            "gender": "male",
            "google_id": google_id,
        }

    async def test_bare_client_google_id_is_rejected(self, mdb):
        with pytest.raises(ValidationException):
            await auth_service.register_candidate(mdb, self._data(google_id="victim-google-id"))
        assert await mdb.users.count_documents({}) == 0

    async def test_token_for_another_email_is_rejected(self, mdb):
        tok = self._reg(email="someone@else.com")
        with pytest.raises(ValidationException):
            await auth_service.register_candidate(mdb, self._data(google_id=tok))

    async def test_expired_or_forged_token_rejected(self, mdb):
        with pytest.raises(ValidationException):
            await auth_service.register_candidate(mdb, self._data(google_id="a.b.c"))

    async def test_valid_token_registers_verified_account(self, mdb):
        await auth_service.register_candidate(mdb, self._data(google_id=self._reg()))
        u = await mdb.users.find_one({})
        assert u["google_id"] == "google-sub-1" and u["email_verified"] is True

    async def test_access_token_cannot_be_used_as_registration_token(self, mdb):
        access = create_access_token({"sub": "x", "role": "candidate", "email": "new@g.com"})
        with pytest.raises(ValidationException):
            await auth_service.register_candidate(mdb, self._data(google_id=access))

    def _patch_google(self, info):
        resp = MagicMock(status_code=200)
        resp.json.return_value = info
        client = MagicMock()
        client.get = AsyncMock(return_value=resp)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        return patch("app.components.auth.auth_service.httpx.AsyncClient", return_value=cm)

    async def test_unverified_google_email_rejected(self, mdb):
        from app.core.config import settings

        info = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "x@g.com",
            "email_verified": "false",
            "sub": "s",
        }
        with self._patch_google(info), pytest.raises(UnauthorizedException):
            await auth_service.google_auth(mdb, "cred")

    async def test_verified_new_user_gets_signed_registration_token(self, mdb):
        from app.core.config import settings

        info = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "X@G.com",
            "email_verified": "true",
            "sub": "s-9",
        }
        with self._patch_google(info):
            out = await auth_service.google_auth(mdb, "cred")
        assert out.needs_registration and out.google_data.email == "x@g.com"
        assert (
            auth_service._verify_google_registration_token(out.google_data.google_id, "x@g.com")
            == "s-9"
        )

    async def test_deactivated_candidate_cannot_google_login(self, mdb):
        from app.core.config import settings

        await _user(mdb, "off@g.com", UserRole.CANDIDATE, is_active=False)
        info = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "off@g.com",
            "email_verified": "true",
            "sub": "s",
        }
        with self._patch_google(info), pytest.raises(UnauthorizedException):
            await auth_service.google_auth(mdb, "cred")


class TestSetupAtomic:
    def _data(self, email):
        return {"first_name": "Su", "last_name": "P", "email": email, "password": PW}

    async def test_second_setup_is_refused(self, mdb):
        await auth_service.setup_super_admin(mdb, self._data("one@x.com"))
        with pytest.raises(ForbiddenException):
            await auth_service.setup_super_admin(mdb, self._data("two@x.com"))
        assert await mdb.users.count_documents({"role": UserRole.SUPER_ADMIN}) == 1

    async def test_parallel_setups_create_exactly_one(self, mdb):
        results = await asyncio.gather(
            *(auth_service.setup_super_admin(mdb, self._data(f"s{i}@x.com")) for i in range(6)),
            return_exceptions=True,
        )
        assert sum(1 for r in results if not isinstance(r, Exception)) == 1
        assert all(isinstance(r, ForbiddenException) for r in results if isinstance(r, Exception))
        assert await mdb.users.count_documents({"role": UserRole.SUPER_ADMIN}) == 1

    async def test_failed_creation_releases_the_marker(self, mdb):
        with patch.object(auth_service, "hash_password", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                await auth_service.setup_super_admin(mdb, self._data("f@x.com"))
        assert (await auth_service.setup_super_admin(mdb, self._data("ok@x.com"))).access_token


# ═══════════════════════════ 5. error leakage / ids ═══════════════════════════


class TestErrorHandling:
    def test_unhandled_exception_does_not_leak_text(self, mdb):
        app = create_application()
        app.dependency_overrides[get_db] = lambda: mdb

        @app.get("/api/_boom")
        async def boom() -> dict:
            raise RuntimeError("mongodb://user:secret@10.0.0.5 stack details")

        r = TestClient(app, raise_server_exceptions=False).get("/api/_boom")
        assert r.status_code == 500
        assert "secret" not in r.text and "10.0.0.5" not in r.text
        assert r.json()["message"] == "Internal server error"

    def test_invalid_object_id_is_422(self, client, world):
        r = client.get("/api/users/candidates/not-an-object-id", headers=_tok(world["sup"]))
        assert r.status_code == 422
        assert "InvalidId" not in r.text and "ObjectId" not in r.text

    def test_candidate_livekit_failure_is_generic(self, client, world):
        with patch(
            "app.components.livekit.livekit_service.generate_candidate_token",
            AsyncMock(side_effect=RuntimeError("livekit key=SECRET host=10.1.1.1")),
        ):
            r = client.post(
                f"/api/candidate/submission/{world['sub_a']}/livekit-token",
                headers=_tok(world["cand_a"]),
            )
        assert r.status_code == 503
        assert "SECRET" not in r.text and "10.1.1.1" not in r.text

    def test_livekit_missing_submission_stays_404(self, client, world):
        with patch(
            "app.components.livekit.livekit_service.generate_candidate_token",
            AsyncMock(side_effect=NotFoundException("Submission not found")),
        ):
            r = client.post(
                f"/api/candidate/submission/{ObjectId()}/livekit-token",
                headers=_tok(world["cand_a"]),
            )
        assert r.status_code == 404


# ═══════════════════════════ 6. candidate scoping + answer key ═══════════════════════════


class TestCandidateScoping:
    async def test_list_get_update_delete_are_workspace_scoped(self, mdb, world):
        a, b = world["adm_a"], world["adm_b"]
        sup = world["sup"]
        ids = {
            c["id"] for c in (await user_service.list_candidates(mdb, scope_user=a))["candidates"]
        }
        assert ids == {str(world["cand_a"]["_id"])}
        ids_sup = {
            c["id"] for c in (await user_service.list_candidates(mdb, scope_user=sup))["candidates"]
        }
        assert {str(world["cand_a"]["_id"]), str(world["cand_b"]["_id"])} <= ids_sup

        foreign = str(world["cand_b"]["_id"])
        with pytest.raises(NotFoundException):
            await user_service.get_candidate(mdb, foreign, a)
        with pytest.raises(NotFoundException):
            await user_service.update_candidate(mdb, foreign, {"first_name": "Hacked"}, a)
        with pytest.raises(NotFoundException):
            await user_service.delete_candidate(mdb, foreign, a)
        with pytest.raises(NotFoundException):
            await user_service.get_candidate_submission_history(mdb, foreign, a)
        victim = await mdb.users.find_one({"_id": world["cand_b"]["_id"]})
        assert victim["first_name"] == "F" and victim["is_active"] is True
        assert (await user_service.get_candidate(mdb, foreign, b)).id == foreign
        assert (await user_service.get_candidate(mdb, foreign, sup)).id == foreign

    async def test_submission_history_hides_other_tenants_attempts(self, mdb, world):
        # candidate A also took an assessment of tenant B
        await mdb.assessment_submissions.insert_one(
            {
                "assessment_id": world["asm_b"],
                "candidate_id": world["cand_a"]["_id"],
                "status": "completed",
                "started_at": utcnow(),
            }
        )
        hist_a = await user_service.get_candidate_submission_history(
            mdb, str(world["cand_a"]["_id"]), world["adm_a"]
        )
        assert {h["assessment_name"] for h in hist_a} == {"Asm a"}
        hist_sup = await user_service.get_candidate_submission_history(
            mdb, str(world["cand_a"]["_id"]), world["sup"]
        )
        assert {h["assessment_name"] for h in hist_sup} == {"Asm a", "Asm b"}

    async def test_admin_sees_candidates_they_created(self, mdb, world):
        created = await user_service.create_candidate_from_admin(
            mdb,
            {"first_name": "New", "last_name": "C", "email": "Fresh@X.com"},
            str(world["adm_a"]["_id"]),
        )
        a = {**world["adm_a"], "_id": str(world["adm_a"]["_id"])}
        assert created.email == "fresh@x.com"
        assert (await user_service.get_candidate(mdb, created.id, a)).id == created.id
        with pytest.raises(NotFoundException):
            await user_service.get_candidate(mdb, created.id, world["adm_b"])

    def test_router_returns_404_for_foreign_candidate(self, client, world):
        r = client.get(
            f"/api/users/candidates/{world['cand_b']['_id']}", headers=_tok(world["adm_a"])
        )
        assert r.status_code == 404
        r = client.delete(
            f"/api/users/candidates/{world['cand_b']['_id']}", headers=_tok(world["adm_a"])
        )
        assert r.status_code == 404
        r = client.get("/api/users/candidates", headers=_tok(world["adm_a"]))
        assert {c["id"] for c in r.json()["data"]["candidates"]} == {str(world["cand_a"]["_id"])}


class TestAnswerKeySnapshot:
    async def _setup(self, mdb, ws):
        q = {
            "question_text": "Pick the right one",
            "question_type": "mcq_single",
            "complexity": "low",
            "options": [
                {"id": "o1", "text": "right", "is_correct": True},
                {"id": "o2", "text": "wrong", "is_correct": False},
            ],
            "correct_answer": None,
        }
        q["_id"] = (await mdb.questions.insert_one(dict(q))).inserted_id
        asm = (
            await mdb.assessments.insert_one(
                {
                    "workspace_id": ws,
                    "name": "S",
                    "share_link": "snap-link",
                    "is_active": True,
                    "rounds": [
                        {
                            "round_number": 1,
                            "question_count": 1,
                            "max_duration_minutes": 5,
                            "question_ids": [q["_id"]],
                        }
                    ],
                    "monitoring_config": {},
                }
            )
        ).inserted_id
        return q, asm

    async def test_snapshot_is_stored_but_never_sent_to_the_candidate(self, mdb, world):
        q, _ = await self._setup(mdb, world["ws_a"])
        cand = world["cand_a"]
        started = await candidate_service.start_assessment(mdb, "snap-link", str(cand["_id"]))
        for rd in started["rounds_data"]:
            for qq in rd["questions"]:
                assert "answer_key" not in qq
                assert all("is_correct" not in o for o in qq["options"])
        stored = await mdb.assessment_submissions.find_one({"_id": ObjectId(started["id"])})
        key = stored["rounds_data"][0]["questions"][0]["answer_key"]
        assert {o["id"] for o in key["options"] if o["is_correct"]} == {"o1"}
        assert key["question_text"] == "Pick the right one"

        again = await candidate_service.start_assessment(mdb, "snap-link", str(cand["_id"]))
        assert "answer_key" not in str(again["rounds_data"])
        with patch.object(
            candidate_service,
            "_resolve_questions_images",
            wraps=candidate_service._resolve_questions_images,
        ):
            rnd = await candidate_service.get_current_round(mdb, started["id"], str(cand["_id"]))
        assert "answer_key" not in str(rnd)

    async def test_snapshot_survives_question_edit_and_delete(self, mdb, world):
        q, _ = await self._setup(mdb, world["ws_a"])
        cand = world["cand_a"]
        started = await candidate_service.start_assessment(mdb, "snap-link", str(cand["_id"]))
        await mdb.questions.update_one(
            {"_id": q["_id"]},
            {
                "$set": {
                    "options": [
                        {"id": "o1", "text": "right", "is_correct": False},
                        {"id": "o2", "text": "wrong", "is_correct": True},
                    ]
                }
            },
        )
        await mdb.questions.delete_one({"_id": q["_id"]})
        stored = await mdb.assessment_submissions.find_one({"_id": ObjectId(started["id"])})
        key = stored["rounds_data"][0]["questions"][0]["answer_key"]
        assert {o["id"] for o in key["options"] if o["is_correct"]} == {"o1"}


# ═══════════════════════════ 7. CORS warning ═══════════════════════════


class TestCorsWarning:
    def test_regex_triggers_loud_warning(self):
        from loguru import logger

        from app.core import lifespan as ls
        from app.core.config import settings

        seen: list[str] = []
        sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
        try:
            with patch.object(settings, "CORS_ORIGIN_REGEX", r"https://.*\.devtunnels\.ms"):
                ls._warn_on_risky_cors()
        finally:
            logger.remove(sink)
        assert any("CORS_ORIGIN_REGEX" in m for m in seen)

    def test_clean_config_is_silent(self):
        from loguru import logger

        from app.core import lifespan as ls
        from app.core.config import settings

        seen: list[str] = []
        sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
        try:
            with (
                patch.object(settings, "CORS_ORIGIN_REGEX", ""),
                patch.object(settings, "CORS_ORIGINS", ["https://app.example.com"]),
            ):
                ls._warn_on_risky_cors()
        finally:
            logger.remove(sink)
        assert not seen
