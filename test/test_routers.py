"""Integration-style tests for key router endpoints using FastAPI TestClient."""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl
import pytest
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from app.common.constants.app_constants import UserRole
from app.common.utils import utcnow
from app.components.auth.auth_service import create_access_token, hash_password
from app.core.dependencies import get_db
from app.factory import create_application

_ADMIN_EMAIL = "admin@example.com"
_ADMIN_PASSWORD = "AdminPass@1!"  # NOSONAR - test fixture credential, not a real secret
_CAND_PASSWORD = "CandPass@1!"  # NOSONAR
_NEW_PASS = "NewPass@123!"  # NOSONAR
_ROOT_PASS = "RootPass@123!"  # NOSONAR
_ADMIN_CREATE_PASS = "Pass@1234!"  # NOSONAR
_REGISTER_PASS = "Pass@123!"  # NOSONAR


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_db"]


@pytest.fixture
def app(mock_db):
    application = create_application()
    application.dependency_overrides[get_db] = lambda: mock_db
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
async def seeded_admin(mock_db):
    doc = {
        "first_name": "Admin",
        "last_name": "Test",
        "email": _ADMIN_EMAIL,
        "password_hash": hash_password(_ADMIN_PASSWORD),
        "role": UserRole.SUPER_ADMIN,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": [],
        "default_workspace_id": None,
        "candidate_data": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
def admin_token(seeded_admin):
    return create_access_token(
        {
            "sub": str(seeded_admin["_id"]),
            "role": UserRole.SUPER_ADMIN,
            "email": seeded_admin["email"],
        }
    )


@pytest.fixture
async def seeded_candidate(mock_db):
    doc = {
        "first_name": "Cand",
        "last_name": "User",
        "email": "cand@example.com",
        "password_hash": hash_password(_CAND_PASSWORD),
        "role": UserRole.CANDIDATE,
        "is_active": True,
        "email_verified": False,
        "workspace_ids": [],
        "default_workspace_id": None,
        "candidate_data": {
            "candidate_type": "student",
            "google_id": None,
            "phone": "9999999999",
            "dob": None,
            "gender": "male",
            "institution": None,
            "location": None,
        },
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
def candidate_token(seeded_candidate):
    return create_access_token(
        {
            "sub": str(seeded_candidate["_id"]),
            "role": UserRole.CANDIDATE,
            "email": seeded_candidate["email"],
        }
    )


@pytest.fixture
async def seeded_workspace(mock_db, seeded_admin):
    doc = {
        "name": "Test WS",
        "description": "Router test workspace",
        "created_by": seeded_admin["_id"],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.workspaces.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def seeded_category(mock_db, seeded_admin):
    doc = {
        "name": "Python",
        "description": "Python questions",
        "created_by": seeded_admin["_id"],
        "question_count": 0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.question_categories.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def seeded_question(mock_db, seeded_category, seeded_admin):
    doc = {
        "category_id": seeded_category["_id"],
        "question_text": "What is Python?",
        "question_type": "mcq_single",
        "complexity": "low",
        "options": [{"id": "a", "text": "A language", "is_correct": True}],
        "correct_answer": None,
        "created_by": seeded_admin["_id"],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.questions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def seeded_assessment(mock_db, seeded_workspace, seeded_admin):
    from app.common.utils import encode_permanent_sharelink

    # Insert without share_link first to get the ID
    doc = {
        "workspace_id": seeded_workspace["_id"],
        "name": "Router Assessment",
        "description": "",
        "rounds": [
            {"round_number": 1, "question_count": 1, "max_duration_minutes": 30, "question_ids": []}
        ],
        "accessibility": "normal",
        "monitoring_config": None,
        "is_active": True,
        "created_by": seeded_admin["_id"],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.assessments.insert_one(doc)
    share_link = encode_permanent_sharelink(str(result.inserted_id))
    await mock_db.assessments.update_one(
        {"_id": result.inserted_id}, {"$set": {"share_link": share_link}}
    )
    doc["_id"] = result.inserted_id
    doc["share_link"] = share_link
    return doc


@pytest.fixture
async def seeded_submission(mock_db, seeded_assessment, seeded_candidate):
    from app.common.constants.app_constants import SubmissionStatus

    doc = {
        "assessment_id": seeded_assessment["_id"],
        "candidate_id": seeded_candidate["_id"],
        "share_id": None,
        "monitoring_overrides": None,
        "status": SubmissionStatus.COMPLETED,
        "current_round": 1,
        "rounds_data": [],
        "score": 0,
        "percentage": 80.0,
        "screenshots": [],
        "malpractice_count": 0,
        "malpractice_events": [],
        "reaccess_count": 0,
        "started_at": utcnow(),
        "completed_at": utcnow(),
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.assessment_submissions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def seeded_in_progress_submission(mock_db, seeded_assessment, seeded_candidate):
    from app.common.constants.app_constants import SubmissionStatus

    doc = {
        "assessment_id": seeded_assessment["_id"],
        "candidate_id": seeded_candidate["_id"],
        "share_id": None,
        "monitoring_overrides": None,
        "status": SubmissionStatus.IN_PROGRESS,
        "current_round": 1,
        "rounds_data": [
            {
                "round_number": 1,
                "question_count": 1,
                "max_duration_minutes": 30,
                "questions": [],
                "answers": {},
                "completed": False,
                "started_at": utcnow(),
            }
        ],
        "score": 0,
        "percentage": 0.0,
        "screenshots": [],
        "malpractice_count": 0,
        "malpractice_events": [],
        "reaccess_count": 0,
        "remaining_seconds": None,
        "current_question_idx": 0,
        "started_at": utcnow(),
        "completed_at": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.assessment_submissions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def seeded_on_hold_submission(mock_db, seeded_assessment, seeded_candidate):
    from app.common.constants.app_constants import SubmissionStatus

    doc = {
        "assessment_id": seeded_assessment["_id"],
        "candidate_id": seeded_candidate["_id"],
        "share_id": None,
        "monitoring_overrides": None,
        "status": SubmissionStatus.ON_HOLD,
        "current_round": 1,
        "rounds_data": [],
        "score": 0,
        "percentage": 0.0,
        "screenshots": [],
        "malpractice_count": 0,
        "malpractice_events": [],
        "reaccess_count": 0,
        "remaining_seconds": 600,
        "current_question_idx": 2,
        "started_at": utcnow(),
        "paused_at": utcnow(),
        "completed_at": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await mock_db.assessment_submissions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
class TestAuthRoutes:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ok"

    def test_admin_login_success(self, client, seeded_admin):
        resp = client.post(
            "/api/auth/admin/login",
            json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["access_token"]

    def test_admin_login_wrong_password(self, client, seeded_admin):
        resp = client.post(
            "/api/auth/admin/login",
            json={"email": _ADMIN_EMAIL, "password": "Wrong@1!"},  # NOSONAR
        )
        assert resp.status_code == 401

    def test_candidate_login(self, client, seeded_candidate):
        resp = client.post(
            "/api/auth/login",
            json={"email": "cand@example.com", "password": _CAND_PASSWORD},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["access_token"]

    def test_candidate_register(self, client):
        resp = client.post(
            "/api/auth/register",
            json={
                "first_name": "New",
                "last_name": "User",
                "email": "newcandidate@example.com",
                "phone": "9876543210",
                "password": _NEW_PASS,
                "father_name": "John",
                "gender": "male",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["access_token"]

    def test_candidate_register_invalid_email(self, client):
        resp = client.post(
            "/api/auth/register",
            json={
                "first_name": "Bad",
                "email": "not-an-email",
                "password": _REGISTER_PASS,
                "father_name": "Someone",
                "gender": "male",
            },
        )
        assert resp.status_code == 422

    def test_refresh_token(self, client, seeded_admin):
        login = client.post(
            "/api/auth/admin/login",
            json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
        )
        refresh_token = login.json()["data"]["refresh_token"]
        resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert resp.json()["data"]["access_token"]

    def test_logout(self, client, seeded_admin):
        login = client.post(
            "/api/auth/admin/login",
            json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
        )
        refresh_token = login.json()["data"]["refresh_token"]
        resp = client.post("/api/auth/logout", json={"refresh_token": refresh_token})
        assert resp.status_code == 200

    def test_get_me(self, client, seeded_admin, admin_token):
        resp = client.get("/api/auth/me", headers=auth_headers(admin_token))
        assert resp.status_code == 200
        assert resp.json()["data"]["email"] == _ADMIN_EMAIL

    def test_setup_super_admin(self, client):
        resp = client.post(
            "/api/auth/setup",
            json={
                "first_name": "Root",
                "last_name": "Admin",
                "email": "root@example.com",
                "password": _ROOT_PASS,
            },
        )
        assert resp.status_code == 200

    def test_google_login(self, client):
        from app.core.config import settings

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "email": "google@example.com",
            "given_name": "Google",
            "family_name": "User",
            "sub": "google_sub_123",
            "aud": settings.GOOGLE_CLIENT_ID,
        }
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client_instance

        with patch("app.components.auth.auth_service.httpx", mock_httpx):
            resp = client.post("/api/auth/google", json={"credential": "google_token"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Role protection
# ---------------------------------------------------------------------------
class TestRoleProtection:
    def test_admin_endpoint_requires_auth(self, client):
        resp = client.get("/api/questions/categories")
        assert resp.status_code == 401

    def test_admin_endpoint_rejects_invalid_token(self, client):
        resp = client.get(
            "/api/questions/categories",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# User routes
# ---------------------------------------------------------------------------
class TestUserRoutes:
    def test_list_users(self, client, seeded_admin, admin_token):
        resp = client.get("/api/users", headers=auth_headers(admin_token))
        assert resp.status_code == 200

    def test_create_user(self, client, seeded_admin, seeded_workspace, admin_token):
        resp = client.post(
            "/api/users",
            headers=auth_headers(admin_token),
            json={
                "first_name": "New",
                "last_name": "Admin",
                "email": "newadmin@example.com",
                "password": _ADMIN_CREATE_PASS,
                "role": "admin",
                "workspace_ids": [str(seeded_workspace["_id"])],
            },
        )
        assert resp.status_code == 200

    def test_get_user(self, client, seeded_admin, admin_token):
        resp = client.get(f"/api/users/{seeded_admin['_id']}", headers=auth_headers(admin_token))
        assert resp.status_code == 200

    def test_update_user(self, client, seeded_admin, admin_token):
        resp = client.put(
            f"/api/users/{seeded_admin['_id']}",
            headers=auth_headers(admin_token),
            json={"first_name": "Updated"},
        )
        assert resp.status_code == 200

    def test_patch_user(self, client, seeded_admin, admin_token):
        resp = client.patch(
            f"/api/users/{seeded_admin['_id']}",
            headers=auth_headers(admin_token),
            json={"last_name": "Patched"},
        )
        assert resp.status_code == 200

    def test_update_me(self, client, seeded_admin, admin_token):
        resp = client.patch(
            "/api/users/me",
            headers=auth_headers(admin_token),
            json={"first_name": "Me Updated"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Workspace routes
# ---------------------------------------------------------------------------
class TestWorkspaceRoutes:
    def test_list_workspaces(self, client, seeded_admin, seeded_workspace, admin_token):
        resp = client.get("/api/workspaces", headers=auth_headers(admin_token))
        assert resp.status_code == 200

    def test_create_workspace(self, client, seeded_admin, admin_token):
        resp = client.post(
            "/api/workspaces",
            headers=auth_headers(admin_token),
            json={"name": "New Workspace", "description": "Created via test"},
        )
        assert resp.status_code == 200

    def test_get_workspace(self, client, seeded_admin, seeded_workspace, admin_token):
        resp = client.get(
            f"/api/workspaces/{seeded_workspace['_id']}",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_update_workspace(self, client, seeded_admin, seeded_workspace, admin_token):
        resp = client.put(
            f"/api/workspaces/{seeded_workspace['_id']}",
            headers=auth_headers(admin_token),
            json={"name": "Updated WS"},
        )
        assert resp.status_code == 200

    def test_invite_members(self, client, seeded_admin, seeded_workspace, admin_token):
        resp = client.post(
            f"/api/workspaces/{seeded_workspace['_id']}/invite",
            headers=auth_headers(admin_token),
            json={"user_ids": [str(seeded_admin["_id"])]},
        )
        assert resp.status_code == 200

    def test_get_members(self, client, seeded_admin, seeded_workspace, admin_token):
        resp = client.get(
            f"/api/workspaces/{seeded_workspace['_id']}/members",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_delete_workspace(self, client, seeded_admin, seeded_workspace, admin_token):
        resp = client.delete(
            f"/api/workspaces/{seeded_workspace['_id']}",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_list_admin_users(self, client, seeded_admin, admin_token):
        resp = client.get("/api/workspaces/admin-users", headers=auth_headers(admin_token))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Question routes
# ---------------------------------------------------------------------------
class TestQuestionRoutes:
    def test_list_categories(self, client, seeded_admin, seeded_category, admin_token):
        resp = client.get("/api/questions/categories", headers=auth_headers(admin_token))
        assert resp.status_code == 200

    def test_create_category(self, client, seeded_admin, admin_token):
        resp = client.post(
            "/api/questions/categories",
            headers=auth_headers(admin_token),
            json={"name": "JavaScript", "description": "JS questions"},
        )
        assert resp.status_code == 200

    def test_update_category(self, client, seeded_admin, seeded_category, admin_token):
        resp = client.put(
            f"/api/questions/categories/{seeded_category['_id']}",
            headers=auth_headers(admin_token),
            json={"name": "Updated Python"},
        )
        assert resp.status_code == 200

    def test_delete_category(self, client, seeded_admin, seeded_category, admin_token):
        resp = client.delete(
            f"/api/questions/categories/{seeded_category['_id']}",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_list_questions(
        self, client, seeded_admin, seeded_category, seeded_question, admin_token
    ):
        resp = client.get(
            f"/api/questions/categories/{seeded_category['_id']}/questions",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_create_question(self, client, seeded_admin, seeded_category, admin_token):
        resp = client.post(
            f"/api/questions/categories/{seeded_category['_id']}/questions",
            headers=auth_headers(admin_token),
            json={
                "question_text": "What is a list?",
                "question_type": "mcq_single",
                "complexity": "low",
                "options": [{"id": "a", "text": "A data structure", "is_correct": True}],
                "correct_answer": None,
            },
        )
        assert resp.status_code == 200

    def test_bulk_create_questions(self, client, seeded_admin, seeded_category, admin_token):
        resp = client.post(
            f"/api/questions/categories/{seeded_category['_id']}/bulk",
            headers=auth_headers(admin_token),
            json={
                "questions": [
                    {
                        "question_text": "Bulk Q1",
                        "question_type": "essay",
                        "complexity": "low",
                        "options": [],
                        "correct_answer": "Answer 1",
                    }
                ]
            },
        )
        assert resp.status_code == 200

    def test_update_question(self, client, seeded_admin, seeded_question, admin_token):
        resp = client.put(
            f"/api/questions/{seeded_question['_id']}",
            headers=auth_headers(admin_token),
            json={
                "question_text": "Updated question text",
                "question_type": "mcq_single",
                "options": [{"id": "a", "text": "Updated answer", "is_correct": True}],
            },
        )
        assert resp.status_code == 200

    def test_delete_question(self, client, seeded_admin, seeded_question, admin_token):
        resp = client.delete(
            f"/api/questions/{seeded_question['_id']}",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_ai_generate(self, client, seeded_admin, seeded_category, admin_token):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.choices[
            0
        ].message.content = (
            '[{"question_text": "Q?", "options": [{"id": "a", "text": "A", "is_correct": true}]}]'
        )
        mock_client.chat.completions.create.return_value = mock_msg

        with patch.dict("sys.modules", {"openai": mock_openai}):
            resp = client.post(
                f"/api/questions/categories/{seeded_category['_id']}/ai-generate",
                headers=auth_headers(admin_token),
                json={
                    "topic": "Python",
                    "count": 1,
                    "complexity": "low",
                    "question_type": "mcq_single",
                },
            )
        assert resp.status_code == 200

    def test_excel_import(self, client, seeded_admin, seeded_category, admin_token):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Question", "Options", "Answer", "Complexity"])
        ws.append(["Test question?", "A, B", "1", "low"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        resp = client.post(
            f"/api/questions/categories/{seeded_category['_id']}/excel-import",
            headers=auth_headers(admin_token),
            files={
                "file": (
                    "test.xlsx",
                    buf,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"column_map": "{}"},
        )
        assert resp.status_code == 200

    def test_excel_import_invalid_column_map(
        self, client, seeded_admin, seeded_category, admin_token
    ):
        """Invalid JSON in column_map falls back to empty dict."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Question"])
        ws.append(["Test?"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        resp = client.post(
            f"/api/questions/categories/{seeded_category['_id']}/excel-import",
            headers=auth_headers(admin_token),
            files={
                "file": (
                    "test.xlsx",
                    buf,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"column_map": "not-json"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Assessment routes
# ---------------------------------------------------------------------------
class TestAssessmentRoutes:
    def test_list_assessments(
        self, client, seeded_admin, seeded_workspace, seeded_assessment, admin_token
    ):
        resp = client.get(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_create_assessment(self, client, seeded_admin, seeded_workspace, admin_token):
        resp = client.post(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments",
            headers=auth_headers(admin_token),
            json={
                "name": "New Assessment",
                "rounds": [
                    {
                        "round_number": 1,
                        "question_count": 5,
                        "max_duration_minutes": 45,
                        "question_ids": [],
                    }
                ],
                "accessibility": "normal",
            },
        )
        assert resp.status_code == 200

    def test_get_assessment(
        self, client, seeded_admin, seeded_workspace, seeded_assessment, admin_token
    ):
        resp = client.get(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_update_assessment(
        self, client, seeded_admin, seeded_workspace, seeded_assessment, admin_token
    ):
        resp = client.put(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}",
            headers=auth_headers(admin_token),
            json={"name": "Updated Assessment"},
        )
        assert resp.status_code == 200

    def test_delete_assessment(
        self, client, seeded_admin, seeded_workspace, seeded_assessment, admin_token
    ):
        resp = client.delete(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_list_submissions(
        self, client, seeded_admin, seeded_workspace, seeded_assessment, admin_token
    ):
        resp = client.get(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/submissions",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_get_submission(
        self,
        client,
        seeded_admin,
        seeded_workspace,
        seeded_assessment,
        seeded_submission,
        admin_token,
    ):
        resp = client.get(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/submissions/{seeded_submission['_id']}",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_grant_reaccess(
        self,
        client,
        seeded_admin,
        seeded_workspace,
        seeded_assessment,
        seeded_submission,
        admin_token,
    ):
        resp = client.post(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/submissions/{seeded_submission['_id']}/reaccess",
            headers=auth_headers(admin_token),
            json={
                "reason": "Technical issue during the session",
                "reason_category": "technical_issue",
            },
        )
        assert resp.status_code == 200

    def test_export_submissions(
        self, client, seeded_admin, seeded_workspace, seeded_assessment, admin_token
    ):
        resp = client.get(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/submissions/export",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_terminate_submission(
        self,
        client,
        seeded_admin,
        seeded_workspace,
        seeded_assessment,
        seeded_in_progress_submission,
        admin_token,
    ):
        with patch("app.components.scoring.scoring_tasks.calculate_and_store_score", AsyncMock()):
            resp = client.post(
                f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/submissions/{seeded_in_progress_submission['_id']}/terminate",
                headers=auth_headers(admin_token),
                json={"reason": "Policy violation"},
            )
        assert resp.status_code == 200

    def test_force_complete_submission(
        self,
        client,
        seeded_admin,
        seeded_workspace,
        seeded_assessment,
        seeded_in_progress_submission,
        admin_token,
    ):
        with patch("app.components.scoring.scoring_tasks.calculate_and_store_score", AsyncMock()):
            resp = client.post(
                f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/submissions/{seeded_in_progress_submission['_id']}/complete",
                headers=auth_headers(admin_token),
            )
        assert resp.status_code == 200

    def test_resume_interview(
        self,
        client,
        seeded_admin,
        seeded_workspace,
        seeded_assessment,
        seeded_on_hold_submission,
        admin_token,
    ):
        resp = client.post(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/submissions/{seeded_on_hold_submission['_id']}/resume",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_create_share(
        self, client, seeded_admin, seeded_workspace, seeded_assessment, admin_token
    ):
        resp = client.post(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/shares",
            headers=auth_headers(admin_token),
            json={"label": "Test Share Link", "monitoring_overrides": None},
        )
        assert resp.status_code == 200

    def test_list_shares(
        self, client, seeded_admin, seeded_workspace, seeded_assessment, admin_token
    ):
        resp = client.get(
            f"/api/workspaces/{seeded_workspace['_id']}/assessments/{seeded_assessment['_id']}/shares",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_validate_share_link(self, client, seeded_assessment):
        share_link = seeded_assessment["share_link"]
        resp = client.get(f"/api/assessments/share/validate?link={share_link}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "can_allow" in data

    def test_validate_invalid_share_link(self, client):
        resp = client.get("/api/assessments/share/validate?link=invalid-garbage-link")
        assert resp.status_code == 200
        assert resp.json()["data"]["can_allow"] is False


# ---------------------------------------------------------------------------
# Candidate routes
# ---------------------------------------------------------------------------
class TestCandidateRoutes:
    def test_get_assessment_by_share_link(
        self, client, seeded_assessment, seeded_candidate, candidate_token
    ):
        share_link = seeded_assessment["share_link"]
        resp = client.get(
            f"/api/candidate/assessment/{share_link}",
            headers=auth_headers(candidate_token),
        )
        assert resp.status_code == 200

    def test_start_assessment(self, client, seeded_candidate, seeded_assessment, candidate_token):
        share_link = seeded_assessment["share_link"]
        resp = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"]

    def test_get_current_round(self, client, seeded_candidate, seeded_assessment, candidate_token):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        resp = client.get(
            f"/api/candidate/submission/{submission_id}/round",
            headers=auth_headers(candidate_token),
        )
        assert resp.status_code == 200

    def test_submit_answer(self, client, seeded_candidate, seeded_assessment, candidate_token):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        resp = client.post(
            f"/api/candidate/submission/{submission_id}/answer",
            headers=auth_headers(candidate_token),
            json={"question_id": "q_001", "answer": "Option A"},
        )
        assert resp.status_code == 200

    def test_finish_round(self, client, seeded_candidate, seeded_assessment, candidate_token):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        # finish_round uses MongoDB array filters not supported by mongomock — mock at service level
        with (
            patch(
                "app.components.candidate.candidate_service.finish_round",
                AsyncMock(return_value={"completed": True, "finished_round": 1}),
            ),
            patch("app.components.scoring.scoring_tasks.calculate_and_store_score", AsyncMock()),
        ):
            resp = client.post(
                f"/api/candidate/submission/{submission_id}/finish-round",
                headers=auth_headers(candidate_token),
            )
        assert resp.status_code == 200

    def test_save_screenshot(self, client, seeded_candidate, seeded_assessment, candidate_token):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        with patch("app.components.candidate.candidate_service.s3_service") as mock_s3:
            mock_s3.make_screenshot_key.return_value = "screenshots/fake/key.png"
            mock_s3.upload = AsyncMock()
            resp = client.post(
                f"/api/candidate/submission/{submission_id}/screenshot",
                headers=auth_headers(candidate_token),
                files={"file": ("screenshot.png", io.BytesIO(fake_png), "image/png")},
            )
        assert resp.status_code == 200

    def test_save_screenshot_invalid_type(
        self, client, seeded_candidate, seeded_assessment, candidate_token
    ):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        resp = client.post(
            f"/api/candidate/submission/{submission_id}/screenshot",
            headers=auth_headers(candidate_token),
            files={"file": ("doc.pdf", io.BytesIO(b"PDF"), "application/pdf")},
        )
        assert resp.status_code == 422

    def test_save_screenshot_too_large(
        self, client, seeded_candidate, seeded_assessment, candidate_token
    ):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        big_data = b"\x89PNG" + b"\x00" * (2 * 1024 * 1024 + 1)
        resp = client.post(
            f"/api/candidate/submission/{submission_id}/screenshot",
            headers=auth_headers(candidate_token),
            files={"file": ("big.png", io.BytesIO(big_data), "image/png")},
        )
        assert resp.status_code == 422

    def test_flag_malpractice(self, client, seeded_candidate, seeded_assessment, candidate_token):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        resp = client.post(
            f"/api/candidate/submission/{submission_id}/malpractice",
            headers=auth_headers(candidate_token),
            data={"type": "tab_switch"},
        )
        assert resp.status_code == 200

    def test_flag_malpractice_invalid_type(
        self, client, seeded_candidate, seeded_assessment, candidate_token
    ):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        resp = client.post(
            f"/api/candidate/submission/{submission_id}/malpractice",
            headers=auth_headers(candidate_token),
            data={"type": "invalid_type"},
        )
        assert resp.status_code == 422

    def test_get_session_state(self, client, seeded_candidate, seeded_assessment, candidate_token):
        share_link = seeded_assessment["share_link"]
        start = client.post(
            f"/api/candidate/assessment/{share_link}/start",
            headers=auth_headers(candidate_token),
        )
        submission_id = start.json()["data"]["id"]
        resp = client.get(
            f"/api/candidate/submission/{submission_id}/session-state",
            headers=auth_headers(candidate_token),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "status" in data
        assert "current_round" in data

    def test_get_submission_status(
        self, client, seeded_candidate, seeded_assessment, candidate_token
    ):
        share_link = seeded_assessment["share_link"]
        # No submission yet — should return null data
        resp = client.get(
            f"/api/candidate/submission/status?share_link={share_link}",
            headers=auth_headers(candidate_token),
        )
        assert resp.status_code == 200

    def test_get_live_interviews(self, client, seeded_admin, admin_token):
        resp = client.get("/api/candidate/live-interviews", headers=auth_headers(admin_token))
        assert resp.status_code == 200
