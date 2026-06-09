"""Unit tests for app/components/auth/auth_service.py"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.exceptions import ConflictException, ForbiddenException, UnauthorizedException
from app.common.utils import utcnow
from app.components.auth import auth_service
from app.core.config import settings

_TEST_PASSWORD = "Pass@123"  # NOSONAR - test fixture credential, not a real secret
_NEW_PASSWORD = "NewPass@123"  # NOSONAR
_DUP_PASSWORD = "DupPass@123"  # NOSONAR
_ROOT_PASSWORD = "RootPass@123"  # NOSONAR


class TestAdminLogin:
    async def test_success(self, db, super_admin):
        result = await auth_service.admin_login(db, "superadmin@example.com", "SuperPass@123")
        assert result.access_token
        assert result.user.email == "superadmin@example.com"

    async def test_wrong_password(self, db, super_admin):
        with pytest.raises(UnauthorizedException):
            await auth_service.admin_login(db, "superadmin@example.com", "WrongPass")

    async def test_unknown_email(self, db):
        with pytest.raises(UnauthorizedException):
            await auth_service.admin_login(db, "nobody@test.com", "AnyPass@1")

    async def test_deactivated_account(self, db, super_admin):
        await db.users.update_one({"_id": super_admin["_id"]}, {"$set": {"is_active": False}})
        with pytest.raises(UnauthorizedException, match="deactivated"):
            await auth_service.admin_login(db, "superadmin@example.com", "SuperPass@123")


class TestCandidateLogin:
    async def test_success(self, db, candidate_user):
        result = await auth_service.candidate_login(db, "candidate@example.com", "CandPass@123")
        assert result.access_token
        assert result.user.role == "candidate"

    async def test_wrong_password(self, db, candidate_user):
        with pytest.raises(UnauthorizedException):
            await auth_service.candidate_login(db, "candidate@example.com", "BadPass")

    async def test_admin_cannot_use_candidate_login(self, db, super_admin):
        with pytest.raises(UnauthorizedException):
            await auth_service.candidate_login(db, "superadmin@example.com", "SuperPass@123")


class TestRegisterCandidate:
    async def test_success(self, db):
        data = {
            "first_name": "New",
            "last_name": "User",
            "email": "new@example.com",
            "phone": "9876543210",
            "password": _NEW_PASSWORD,
        }
        result = await auth_service.register_candidate(db, data)
        assert result.access_token
        assert result.user.email == "new@example.com"

    async def test_duplicate_email(self, db, candidate_user):
        data = {
            "first_name": "Dup",
            "email": "candidate@example.com",
            "password": _DUP_PASSWORD,
        }
        with pytest.raises(ConflictException):
            await auth_service.register_candidate(db, data)


class TestTokens:
    def test_create_and_decode(self):
        token = auth_service.create_access_token(
            {"sub": "abc123", "role": "admin", "email": "a@b.com"}
        )
        payload = auth_service.decode_access_token(token)
        assert payload["sub"] == "abc123"
        assert payload["type"] == "access"

    def test_invalid_token_raises(self):
        with pytest.raises(UnauthorizedException):
            auth_service.decode_access_token("not.a.token")

    def test_wrong_token_type_raises(self):
        from jose import jwt

        # Manually create a token with type="refresh"
        token = jwt.encode(
            {"sub": "abc", "type": "refresh", "exp": utcnow() + timedelta(days=1)},
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        with pytest.raises(UnauthorizedException, match="Invalid token type"):
            auth_service.decode_access_token(token)


class TestRefreshAccessToken:
    async def test_success(self, db, super_admin):
        tokens = await auth_service.admin_login(db, "superadmin@example.com", "SuperPass@123")
        result = await auth_service.refresh_access_token(db, tokens.refresh_token)
        assert result.access_token

    async def test_expired_token_raises(self, db, super_admin):
        tokens = await auth_service.admin_login(db, "superadmin@example.com", "SuperPass@123")
        # Force expire the token in DB
        from app.common.utils import hash_token

        await db.refresh_tokens.update_one(
            {"token_hash": hash_token(tokens.refresh_token)},
            {"$set": {"expires_at": utcnow() - timedelta(days=1)}},
        )
        with pytest.raises(UnauthorizedException):
            await auth_service.refresh_access_token(db, tokens.refresh_token)

    async def test_invalid_token_raises(self, db):
        with pytest.raises(UnauthorizedException):
            await auth_service.refresh_access_token(db, "nonexistent_token")

    async def test_user_not_found_raises(self, db, super_admin):
        tokens = await auth_service.admin_login(db, "superadmin@example.com", "SuperPass@123")
        # Delete the user to simulate a stale refresh token
        await db.users.delete_one({"_id": super_admin["_id"]})
        with pytest.raises(UnauthorizedException, match="User not found"):
            await auth_service.refresh_access_token(db, tokens.refresh_token)


class TestLogout:
    async def test_logout_removes_token(self, db, super_admin):
        tokens = await auth_service.admin_login(db, "superadmin@example.com", "SuperPass@123")
        await auth_service.logout(db, tokens.refresh_token)
        from app.common.utils import hash_token

        doc = await db.refresh_tokens.find_one({"token_hash": hash_token(tokens.refresh_token)})
        assert doc is None


class TestGoogleAuth:
    def _make_mock_httpx(self, status_code, json_body):
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = json_body

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client_instance
        return mock_httpx

    async def test_new_user_returns_pre_auth_data(self, db):
        mock_httpx = self._make_mock_httpx(
            200,
            {
                "email": "google@example.com",
                "given_name": "Google",
                "family_name": "User",
                "sub": "google123",
                "aud": settings.GOOGLE_CLIENT_ID,
            },
        )
        with patch("app.components.auth.auth_service.httpx", mock_httpx):
            result = await auth_service.google_auth(db, "valid_credential")
        assert result.needs_registration is True
        assert result.google_data.email == "google@example.com"
        assert result.google_data.first_name == "Google"
        assert result.google_data.google_id == "google123"

    async def test_existing_candidate_logs_in(self, db, candidate_user):
        mock_httpx = self._make_mock_httpx(
            200,
            {
                "email": "candidate@example.com",
                "given_name": "Test",
                "family_name": "Candidate",
                "sub": "google_existing",
                "aud": settings.GOOGLE_CLIENT_ID,
            },
        )
        with patch("app.components.auth.auth_service.httpx", mock_httpx):
            result = await auth_service.google_auth(db, "cred")
        assert result.access_token

    async def test_invalid_credential_raises(self, db):
        mock_httpx = self._make_mock_httpx(400, {})
        with patch("app.components.auth.auth_service.httpx", mock_httpx):
            with pytest.raises(UnauthorizedException, match="Invalid Google"):
                await auth_service.google_auth(db, "bad_cred")

    async def test_audience_mismatch_raises(self, db):
        mock_httpx = self._make_mock_httpx(200, {"email": "g@g.com", "aud": "wrong_audience"})
        with patch("app.components.auth.auth_service.httpx", mock_httpx):
            with pytest.raises(UnauthorizedException, match="audience"):
                await auth_service.google_auth(db, "cred")

    async def test_missing_email_raises(self, db):
        mock_httpx = self._make_mock_httpx(200, {"aud": settings.GOOGLE_CLIENT_ID, "sub": "abc"})
        with patch("app.components.auth.auth_service.httpx", mock_httpx):
            with pytest.raises(UnauthorizedException, match="Email not found"):
                await auth_service.google_auth(db, "cred")

    async def test_non_candidate_role_raises(self, db, super_admin):
        mock_httpx = self._make_mock_httpx(
            200,
            {
                "email": "superadmin@example.com",
                "aud": settings.GOOGLE_CLIENT_ID,
                "sub": "abc",
            },
        )
        with patch("app.components.auth.auth_service.httpx", mock_httpx):
            with pytest.raises(ForbiddenException):
                await auth_service.google_auth(db, "cred")


class TestSetupSuperAdmin:
    async def test_success(self, db):
        result = await auth_service.setup_super_admin(
            db,
            {
                "first_name": "Root",
                "last_name": "Admin",
                "email": "root@example.com",
                "password": _ROOT_PASSWORD,
            },
        )
        assert result.access_token

    async def test_already_exists_raises(self, db, super_admin):
        with pytest.raises(ForbiddenException, match="already exists"):
            await auth_service.setup_super_admin(
                db,
                {
                    "first_name": "Another",
                    "email": "another@example.com",
                    "password": _TEST_PASSWORD,
                },
            )
