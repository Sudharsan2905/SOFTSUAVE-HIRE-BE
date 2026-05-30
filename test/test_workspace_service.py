"""Tests for app/components/workspace/workspace_service.py"""

import pytest
from bson import ObjectId

from app.common.constants.app_constants import UserRole
from app.common.exceptions import ForbiddenException, NotFoundException
from app.components.workspace import workspace_service


class TestCreateWorkspace:
    async def test_creates_workspace(self, db, super_admin):
        result = await workspace_service.create_workspace(
            db, {"name": "New WS", "description": "Test"}, str(super_admin["_id"])
        )
        assert result["name"] == "New WS"

    async def test_creates_without_description(self, db, super_admin):
        result = await workspace_service.create_workspace(
            db, {"name": "No Desc"}, str(super_admin["_id"])
        )
        assert result["description"] == ""


class TestGetWorkspaces:
    async def test_super_admin_sees_all(self, db, workspace, super_admin):
        result = await workspace_service.get_workspaces(
            db, str(super_admin["_id"]), UserRole.SUPER_ADMIN
        )
        assert result["pagination"]["total"] >= 1

    async def test_admin_sees_own_workspaces(self, db, workspace, admin_user):
        # Add admin_user as workspace member so the query matches
        await db.workspaces.update_one(
            {"_id": workspace["_id"]},
            {"$push": {"members": {"user_id": admin_user["_id"]}}},
        )
        result = await workspace_service.get_workspaces(db, str(admin_user["_id"]), UserRole.ADMIN)
        assert result["pagination"]["total"] >= 1

    async def test_pagination(self, db, workspace, super_admin):
        result = await workspace_service.get_workspaces(
            db, str(super_admin["_id"]), UserRole.SUPER_ADMIN, page=1, page_size=1
        )
        assert result["pagination"]["page_size"] == 1


class TestGetWorkspace:
    async def test_super_admin_access(self, db, workspace, super_admin):
        result = await workspace_service.get_workspace(
            db, str(workspace["_id"]), str(super_admin["_id"]), UserRole.SUPER_ADMIN
        )
        assert result["name"] == "Test Workspace"

    async def test_admin_member_access(self, db, workspace, admin_user):
        # Add admin_user as a member so the access check passes
        await db.workspaces.update_one(
            {"_id": workspace["_id"]},
            {"$push": {"members": {"user_id": admin_user["_id"]}}},
        )
        result = await workspace_service.get_workspace(
            db, str(workspace["_id"]), str(admin_user["_id"]), UserRole.ADMIN
        )
        assert result["name"] == "Test Workspace"

    async def test_admin_non_member_forbidden(self, db, workspace, super_admin):
        other_admin_id = str(ObjectId())
        with pytest.raises(ForbiddenException):
            await workspace_service.get_workspace(
                db, str(workspace["_id"]), other_admin_id, UserRole.ADMIN
            )

    async def test_not_found_raises(self, db, super_admin):
        with pytest.raises(NotFoundException):
            await workspace_service.get_workspace(
                db, str(ObjectId()), str(super_admin["_id"]), UserRole.SUPER_ADMIN
            )


class TestUpdateWorkspace:
    async def test_updates_name(self, db, workspace, super_admin):
        result = await workspace_service.update_workspace(
            db,
            str(workspace["_id"]),
            {"name": "Updated WS"},
            str(super_admin["_id"]),
            UserRole.SUPER_ADMIN,
        )
        assert result["name"] == "Updated WS"


class TestInviteMembers:
    async def test_invites_new_member(self, db, workspace, admin_user):
        result = await workspace_service.invite_members(
            db, str(workspace["_id"]), [str(admin_user["_id"])], str(admin_user["_id"])
        )
        member_ids = [str(m["user_id"]) for m in result.get("members", [])]
        assert str(admin_user["_id"]) in member_ids

    async def test_skips_existing_member(self, db, workspace, admin_user):
        # admin_user is already a member (via conftest admin_user fixture)
        await workspace_service.invite_members(
            db, str(workspace["_id"]), [str(admin_user["_id"])], str(admin_user["_id"])
        )

    async def test_skips_non_admin_user(self, db, workspace, candidate_user, super_admin):
        result = await workspace_service.invite_members(
            db, str(workspace["_id"]), [str(candidate_user["_id"])], str(super_admin["_id"])
        )
        member_ids = [str(m["user_id"]) for m in result.get("members", [])]
        assert str(candidate_user["_id"]) not in member_ids

    async def test_not_found_raises(self, db, super_admin):
        with pytest.raises(NotFoundException):
            await workspace_service.invite_members(
                db, str(ObjectId()), [str(super_admin["_id"])], str(super_admin["_id"])
            )

    async def test_sets_default_workspace_when_unset(self, db, workspace, super_admin):
        """When a new user has no default_workspace_id, it gets set on invite."""
        new_admin_id = ObjectId()
        from app.common.utils import utcnow

        await db.users.insert_one(
            {
                "_id": new_admin_id,
                "email": "fresh@example.com",
                "role": UserRole.ADMIN,
                "workspaces": [],
                "default_workspace_id": None,
                "candidate_data": None,
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )
        await workspace_service.invite_members(
            db, str(workspace["_id"]), [str(new_admin_id)], str(super_admin["_id"])
        )
        user = await db.users.find_one({"_id": new_admin_id})
        assert user.get("default_workspace_id") == str(workspace["_id"])


class TestGetMembers:
    async def test_returns_members(self, db, workspace, admin_user):
        result = await workspace_service.get_members(db, str(workspace["_id"]))
        assert isinstance(result, list)

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await workspace_service.get_members(db, str(ObjectId()))


class TestDeleteWorkspace:
    async def test_deletes_workspace(self, db, workspace, super_admin):
        await workspace_service.delete_workspace(db, str(workspace["_id"]))
        remaining = await db.workspaces.find_one({"_id": workspace["_id"]})
        assert remaining is None

    async def test_cleans_up_user_workspace_refs(self, db, workspace, admin_user):
        """Deleting workspace removes it from admin user's workspaces list."""
        await workspace_service.delete_workspace(db, str(workspace["_id"]))
        user = await db.users.find_one({"_id": admin_user["_id"]})
        ws_ids = [w["id"] for w in user.get("workspaces", [])]
        assert str(workspace["_id"]) not in ws_ids

    async def test_resets_default_workspace(self, db, workspace, admin_user):
        """When deleted workspace was the default, default_workspace_id is reset."""
        await workspace_service.delete_workspace(db, str(workspace["_id"]))
        user = await db.users.find_one({"_id": admin_user["_id"]})
        assert user.get("default_workspace_id") is None

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await workspace_service.delete_workspace(db, str(ObjectId()))


class TestGetAllAdminUsers:
    async def test_returns_admin_users(self, db, workspace, admin_user):
        result = await workspace_service.get_all_admin_users(db)
        assert isinstance(result, list)
        emails = [u["email"] for u in result]
        assert "admin@example.com" in emails
