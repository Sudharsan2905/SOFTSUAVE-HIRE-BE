"""Tests covering miscellaneous app files: utils, main, logging, config, lifespan, responses."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMain:
    def test_app_import(self):
        import app.main  # noqa: F401

        assert hasattr(app.main, "app")


class TestLogging:
    def test_setup_logging(self):
        from app.core.logging import setup_logging

        setup_logging("DEBUG")
        setup_logging("INFO")


class TestDependencies:
    def test_get_db(self):
        from app.core.dependencies import get_db

        mock_db = MagicMock()
        mock_request = MagicMock()
        mock_request.app.state.db = mock_db
        assert get_db(mock_request) is mock_db


class TestConfig:
    def test_cors_origins_json_string(self):
        from app.core.config import Settings

        s = Settings()
        object.__setattr__(s, "CORS_ORIGINS", '["http://localhost:3000"]')
        s.model_post_init(None)
        assert s.CORS_ORIGINS == ["http://localhost:3000"]

    def test_cors_origins_plain_string(self):
        from app.core.config import Settings

        s = Settings()
        object.__setattr__(s, "CORS_ORIGINS", "http://localhost:3000")
        s.model_post_init(None)
        assert s.CORS_ORIGINS == ["http://localhost:3000"]


class TestLifespan:
    async def test_lifespan_startup_shutdown(self, db):
        from fastapi import FastAPI

        from app.core.lifespan import lifespan

        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=db)
        mock_client.close = MagicMock()

        test_app = FastAPI()
        with (
            patch("app.core.lifespan.AsyncIOMotorClient", return_value=mock_client),
            patch("app.core.lifespan._validate_settings"),
        ):
            async with lifespan(test_app):
                assert hasattr(test_app.state, "db")

        mock_client.close.assert_called_once()

    async def test_create_indexes(self, db):
        from app.core.lifespan import _create_indexes

        await _create_indexes(db)


class TestUtils:
    def test_generate_uuid(self):
        from app.common.utils import generate_uuid

        result = generate_uuid()
        assert len(result) == 36

    def test_serialize_doc_none(self):
        from app.common.utils import serialize_doc

        assert serialize_doc(None) == {}

    def test_serialize_doc_datetime(self):
        from datetime import UTC, datetime

        from app.common.utils import serialize_doc

        now = datetime.now(UTC)
        result = serialize_doc({"ts": now})
        assert isinstance(result["ts"], str)

    async def test_list_paginated(self, db):
        from app.common.utils import list_paginated

        await db.items.insert_many([{"name": "a"}, {"name": "b"}])
        total, docs = await list_paginated(db.items, {}, "name", 1, 0, 10, ["name"])
        assert total == 2
        assert len(docs) == 2

    async def test_list_paginated_fallback_sort(self, db):
        from app.common.utils import list_paginated

        await db.items.insert_one({"name": "x"})
        total, docs = await list_paginated(db.items, {}, "invalid_field", 1, 0, 10, ["name"])
        assert total == 1


class TestResponses:
    def test_error_response(self):
        from app.common.responses import error_response

        result = error_response("err msg", "detail txt")
        assert result["success"] is False
        assert result["message"] == "err msg"
        assert result["detail"] == "detail txt"


class TestAuthDependencies:
    """Cover auth_dependencies.py functions directly (they're plain callables)."""

    def test_require_candidate_success(self):
        from app.components.auth.auth_dependencies import require_candidate

        user = {"role": "candidate", "_id": "abc"}
        assert require_candidate(current_user=user) is user

    def test_require_candidate_forbidden(self):
        from app.common.exceptions import ForbiddenException
        from app.components.auth.auth_dependencies import require_candidate

        with pytest.raises(ForbiddenException):
            require_candidate(current_user={"role": "admin"})

    def test_require_admin_forbidden(self):
        from app.common.exceptions import ForbiddenException
        from app.components.auth.auth_dependencies import require_admin

        with pytest.raises(ForbiddenException):
            require_admin(current_user={"role": "candidate"})

    def test_require_super_admin_forbidden(self):
        from app.common.exceptions import ForbiddenException
        from app.components.auth.auth_dependencies import require_super_admin

        with pytest.raises(ForbiddenException):
            require_super_admin(current_user={"role": "admin"})

    def test_get_current_user_no_sub(self):
        """Token with no 'sub' raises UnauthorizedException (line 21)."""
        from datetime import timedelta

        from fastapi.testclient import TestClient
        from jose import jwt
        from mongomock_motor import AsyncMongoMockClient

        from app.common.utils import utcnow
        from app.core.config import settings
        from app.core.dependencies import get_db
        from app.factory import create_application

        token = jwt.encode(
            {"role": "admin", "type": "access", "exp": utcnow() + timedelta(hours=1)},
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        mock_db = AsyncMongoMockClient()["test_db"]
        app = create_application()
        app.dependency_overrides[get_db] = lambda: mock_db
        client = TestClient(app)
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_get_current_user_not_in_db(self):
        """Token with valid sub but no user in DB (line 25)."""
        from bson import ObjectId
        from fastapi.testclient import TestClient
        from mongomock_motor import AsyncMongoMockClient

        from app.components.auth.auth_service import create_access_token
        from app.core.dependencies import get_db
        from app.factory import create_application

        token = create_access_token({"sub": str(ObjectId()), "role": "admin", "email": "x@x.com"})
        mock_db = AsyncMongoMockClient()["test_db"]
        app = create_application()
        app.dependency_overrides[get_db] = lambda: mock_db
        client = TestClient(app)
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


class TestAuthSchemaValidators:
    def test_password_missing_uppercase(self):
        from pydantic import ValidationError

        from app.components.auth.auth_schemas import CandidateRegisterRequest

        with pytest.raises(ValidationError, match="uppercase"):
            CandidateRegisterRequest(
                first_name="Test",
                email="t@example.com",
                phone="9876543210",
                password="nouppercase1!",
                father_name="Dad",
                gender="male",
            )

    def test_password_missing_lowercase(self):
        from pydantic import ValidationError

        from app.components.auth.auth_schemas import CandidateRegisterRequest

        with pytest.raises(ValidationError, match="lowercase"):
            CandidateRegisterRequest(
                first_name="Test",
                email="t@example.com",
                phone="9876543210",
                password="NOLOWERCASE1!",
                father_name="Dad",
                gender="male",
            )

    def test_password_missing_digit(self):
        from pydantic import ValidationError

        from app.components.auth.auth_schemas import CandidateRegisterRequest

        with pytest.raises(ValidationError, match="digit"):
            CandidateRegisterRequest(
                first_name="Test",
                email="t@example.com",
                phone="9876543210",
                password="NoDigitPass!",
                father_name="Dad",
                gender="male",
            )

    def test_password_missing_special(self):
        from pydantic import ValidationError

        from app.components.auth.auth_schemas import CandidateRegisterRequest

        with pytest.raises(ValidationError, match="special"):
            CandidateRegisterRequest(
                first_name="Test",
                email="t@example.com",
                phone="9876543210",
                password="NoSpecial12",
                father_name="Dad",
                gender="male",
            )

    def test_phone_invalid_chars(self):
        from pydantic import ValidationError

        from app.components.auth.auth_schemas import CandidateRegisterRequest

        with pytest.raises(ValidationError, match="digits"):
            CandidateRegisterRequest(
                first_name="Test",
                email="t@example.com",
                phone="abc-defghij",
                password="ValidPass1!",
                father_name="Dad",
                gender="male",
            )


class TestExceptionHandlers:
    def test_generic_exception_handler(self):
        from fastapi.testclient import TestClient
        from mongomock_motor import AsyncMongoMockClient

        from app.core.dependencies import get_db
        from app.factory import create_application

        mock_db = AsyncMongoMockClient()["test_db"]
        application = create_application()
        application.dependency_overrides[get_db] = lambda: mock_db

        @application.get("/test/boom")
        async def _boom():
            raise RuntimeError("kaboom")

        client = TestClient(application, raise_server_exceptions=False)
        resp = client.get("/test/boom")
        assert resp.status_code == 500
        assert resp.json()["message"] == "Internal server error"


class TestStartupValidation:
    def test_valid_settings_pass(self):
        from app.core.lifespan import _validate_settings

        with patch("app.core.lifespan.settings") as mock_settings:
            mock_settings.JWT_SECRET_KEY = "secret"
            mock_settings.MONGODB_URL = "mongodb://localhost"
            mock_settings.DATABASE_NAME = "mydb"
            _validate_settings()  # must not raise

    def test_missing_setting_raises(self):
        from app.core.lifespan import _validate_settings

        with patch("app.core.lifespan.settings") as mock_settings:
            mock_settings.JWT_SECRET_KEY = ""
            mock_settings.MONGODB_URL = "mongodb://localhost"
            mock_settings.DATABASE_NAME = "mydb"
            with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
                _validate_settings()


class TestHealthEndpoint:
    def test_health_db_unreachable(self):
        """Health returns 200 with database=error when DB is not set up."""
        from fastapi.testclient import TestClient

        from app.factory import create_application

        app = create_application()
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["data"]["database"] == "error"

    def test_health_db_ok(self):
        """Health returns database=ok when DB ping succeeds."""
        from fastapi.testclient import TestClient

        from app.factory import create_application

        mock_db = MagicMock()
        mock_db.command = AsyncMock(return_value={"ok": 1})
        app = create_application()
        app.state.db = mock_db
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["data"]["database"] == "ok"


class TestApiResponseModel:
    def test_success_response_shape(self):
        from app.common.responses import ApiResponse, success_response

        data = success_response("ok", {"key": "value"})
        model = ApiResponse(**data)
        assert model.success is True
        assert model.message == "ok"
        assert model.data == {"key": "value"}

    def test_success_response_no_data(self):
        from app.common.responses import ApiResponse, success_response

        data = success_response("done")
        model = ApiResponse(**data)
        assert model.data is None


class TestRateLimiter:
    def test_limiter_attached_to_app(self):
        from app.factory import create_application

        app = create_application()
        assert hasattr(app.state, "limiter")
