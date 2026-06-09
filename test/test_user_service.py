"""Tests for app/components/users/user_service.py"""

import pytest
from bson import ObjectId

from app.common.constants.app_constants import UserRole
from app.common.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.components.users import user_service

_TEST_PASSWORD = "Pass@123"  # NOSONAR - test fixture credential, not a real secret
_NEW_PASSWORD = "NewPass@123"  # NOSONAR  # pragma: allowlist secret
_SUPER_ADMIN_PASSWORD = "SuperPass@123"  # NOSONAR  # pragma: allowlist secret


class TestCreateAdminUser:
    async def test_creates_admin_with_workspace(self, db, workspace):
        data = {
            "first_name": "New",
            "last_name": "Admin",
            "email": "newadmin@example.com",
            "password": _TEST_PASSWORD,
            "role": UserRole.ADMIN,
            "workspace_ids": [str(workspace["_id"])],
        }
        result = await user_service.create_admin_user(db, data)
        assert result.email == "newadmin@example.com"
        assert result.role == UserRole.ADMIN

    async def test_creates_super_admin_without_workspace(self, db):
        data = {
            "first_name": "Super",
            "email": "super2@example.com",
            "password": _TEST_PASSWORD,
            "role": UserRole.SUPER_ADMIN,
            "workspace_ids": [],
        }
        result = await user_service.create_admin_user(db, data)
        assert result.role == UserRole.SUPER_ADMIN

    async def test_duplicate_email_raises(self, db, super_admin):
        data = {
            "first_name": "Dup",
            "email": "superadmin@example.com",
            "password": _TEST_PASSWORD,
            "role": UserRole.SUPER_ADMIN,
        }
        with pytest.raises(ConflictException):
            await user_service.create_admin_user(db, data)

    async def test_admin_without_workspace_raises(self, db):
        data = {
            "first_name": "No",
            "email": "noworkspace@example.com",
            "password": _TEST_PASSWORD,
            "role": UserRole.ADMIN,
            "workspace_ids": [],
        }
        with pytest.raises(ForbiddenException):
            await user_service.create_admin_user(db, data)

    async def test_admin_invalid_workspace_ids_raises(self, db):
        data = {
            "first_name": "Bad",
            "email": "badws@example.com",
            "password": _TEST_PASSWORD,
            "role": UserRole.ADMIN,
            "workspace_ids": [str(ObjectId())],  # nonexistent workspace
        }
        with pytest.raises(ForbiddenException):
            await user_service.create_admin_user(db, data)

    async def test_skips_duplicate_workspace_member(self, db, workspace, admin_user):
        """If user is already in workspace, should not add again."""
        data = {
            "first_name": "Another",
            "email": "another@example.com",
            "password": _TEST_PASSWORD,
            "role": UserRole.ADMIN,
            "workspace_ids": [str(workspace["_id"])],
        }
        result = await user_service.create_admin_user(db, data)
        assert result.email == "another@example.com"


_DUMMY_ID = str(ObjectId())  # non-existent ID so no user is excluded from list results


class TestListUsers:
    async def test_returns_all_admins(self, db, super_admin):
        result = await user_service.list_users(db, _DUMMY_ID)
        assert len(result) >= 1

    async def test_filters_by_role(self, db, super_admin):
        result = await user_service.list_users(db, _DUMMY_ID, role_filter=UserRole.SUPER_ADMIN)
        assert all(u.role == UserRole.SUPER_ADMIN for u in result)

    async def test_filters_by_is_active(self, db, super_admin):
        result = await user_service.list_users(db, _DUMMY_ID, is_active_filter=True)
        assert all(u.is_active is True for u in result)

    async def test_filters_by_is_active_false(self, db, super_admin):
        await db.users.update_one({"_id": super_admin["_id"]}, {"$set": {"is_active": False}})
        result = await user_service.list_users(db, _DUMMY_ID, is_active_filter=False)
        assert len(result) >= 1


class TestGetUser:
    async def test_success(self, db, super_admin):
        result = await user_service.get_user(db, str(super_admin["_id"]))
        assert result.email == "superadmin@example.com"

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await user_service.get_user(db, str(ObjectId()))


class TestUpdateUser:
    async def test_update_name(self, db, admin_user):
        result = await user_service.update_user(
            db, str(admin_user["_id"]), {"first_name": "Updated"}
        )
        assert result.first_name == "Updated"

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await user_service.update_user(db, str(ObjectId()), {"first_name": "X"})

    async def test_super_admin_status_forbidden(self, db, super_admin):
        with pytest.raises(ForbiddenException, match="status"):
            await user_service.update_user(db, str(super_admin["_id"]), {"is_active": False})

    async def test_super_admin_workspace_forbidden(self, db, super_admin, workspace):
        with pytest.raises(ForbiddenException, match="workspaces"):
            await user_service.update_user(
                db, str(super_admin["_id"]), {"workspace_ids": [str(workspace["_id"])]}
            )

    async def test_update_workspace_ids(self, db, admin_user, workspace):
        result = await user_service.update_user(
            db, str(admin_user["_id"]), {"workspace_ids": [str(workspace["_id"])]}
        )
        assert str(workspace["_id"]) in result.workspace_ids

    async def test_update_default_workspace(self, db, admin_user, workspace):
        ws_id = str(workspace["_id"])
        result = await user_service.update_user(
            db,
            str(admin_user["_id"]),
            {"workspace_ids": [ws_id], "default_workspace_id": ws_id},
        )
        assert result.default_workspace_id == ws_id

    async def test_remove_current_default_workspace(self, db, admin_user, workspace):
        """When workspace_ids excludes the current default, default resets."""
        # admin_user already has workspace as default; reassign to empty
        result = await user_service.update_user(db, str(admin_user["_id"]), {"workspace_ids": []})
        assert result.default_workspace_id is None

    async def test_update_with_no_changes(self, db, admin_user):
        """Empty update dict should still return the user."""
        result = await user_service.update_user(db, str(admin_user["_id"]), {})
        assert result.email == "admin@example.com"

    async def test_update_is_active_for_admin(self, db, workspace, admin_user):
        """Updating is_active for non-super_admin covers line 118."""
        result = await user_service.update_user(db, str(admin_user["_id"]), {"is_active": False})
        assert result.is_active is False

    async def test_remove_user_from_workspace_membership(self, db, workspace, admin_user):
        """Setting workspace_ids=[] removes all workspaces from the user's workspaces list."""
        result = await user_service.update_user(db, str(admin_user["_id"]), {"workspace_ids": []})
        ws_ids = result.workspace_ids
        assert str(workspace["_id"]) not in ws_ids


class TestUpdateMe:
    async def test_update_name(self, db, super_admin):
        result = await user_service.update_me(
            db, str(super_admin["_id"]), {"first_name": "Updated"}
        )
        assert result["first_name"] == "Updated"

    async def test_update_last_name(self, db, super_admin):
        """Covers line 188: update["last_name"] = data["last_name"]."""
        result = await user_service.update_me(db, str(super_admin["_id"]), {"last_name": "NewLast"})
        assert result["last_name"] == "NewLast"

    async def test_update_password(self, db, super_admin):
        result = await user_service.update_me(
            db,
            str(super_admin["_id"]),
            {
                "password": _NEW_PASSWORD,
                "current_password": _SUPER_ADMIN_PASSWORD,
            },
        )
        assert result["email"] == "superadmin@example.com"

    async def test_update_default_workspace_super_admin(self, db, super_admin, workspace):
        result = await user_service.update_me(
            db, str(super_admin["_id"]), {"default_workspace_id": str(workspace["_id"])}
        )
        assert result.get("default_workspace_id") == str(workspace["_id"])

    async def test_update_default_workspace_not_found(self, db, super_admin):
        with pytest.raises(NotFoundException):
            await user_service.update_me(
                db, str(super_admin["_id"]), {"default_workspace_id": str(ObjectId())}
            )

    async def test_update_default_workspace_not_member(self, db, admin_user, workspace):
        """Admin who is not a member of the workspace should get ForbiddenException."""
        other_ws_id = ObjectId()
        await db.workspaces.insert_one({"_id": other_ws_id, "name": "Other"})
        with pytest.raises(ForbiddenException):
            await user_service.update_me(
                db, str(admin_user["_id"]), {"default_workspace_id": str(other_ws_id)}
            )

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await user_service.update_me(db, str(ObjectId()), {"first_name": "X"})

    async def test_no_update_fields(self, db, super_admin):
        result = await user_service.update_me(db, str(super_admin["_id"]), {})
        assert result["email"] == "superadmin@example.com"
