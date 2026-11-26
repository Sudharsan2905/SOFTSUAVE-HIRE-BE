"""Tenant-isolation guards for admin routes.

Every admin route under ``/workspaces/{workspace_id}/assessments/...`` depends on one of
these, which verify (in order) that

1. the admin is a member of ``workspace_id`` (super admins are exempt),
2. ``assessment_id`` belongs to that workspace,
3. ``submission_id`` / ``share_id`` belongs to that assessment.

Anything foreign — or malformed — is reported as 404, never 403, so tenants cannot probe
for the existence of each other's objects.
"""

from typing import Annotated

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import UserRole
from app.common.exceptions import NotFoundException
from app.components.auth.auth_dependencies import AdminUser
from app.core.dependencies import DB

_NOT_FOUND = "Resource not found"


def to_oid(value: str) -> ObjectId:
    """Parse an ObjectId path parameter; malformed ids are a 404, not a 500."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise NotFoundException(_NOT_FOUND) from exc


def is_super_admin(user: dict) -> bool:
    return user.get("role") == UserRole.SUPER_ADMIN.value


def member_workspace_ids(user: dict) -> list[str]:
    return [str(w) for w in (user.get("workspace_ids") or [])]


def can_access_workspace(user: dict, workspace_id: str) -> bool:
    return is_super_admin(user) or str(workspace_id) in member_workspace_ids(user)


async def workspace_access(workspace_id: str, current_user: AdminUser) -> dict:
    to_oid(workspace_id)
    if not can_access_workspace(current_user, workspace_id):
        raise NotFoundException(_NOT_FOUND)
    return current_user


async def assessment_access(
    workspace_id: str, assessment_id: str, db: DB, current_user: AdminUser
) -> dict:
    await workspace_access(workspace_id, current_user)
    doc = await db.assessments.find_one(
        {"_id": to_oid(assessment_id), "workspace_id": to_oid(workspace_id)}, {"_id": 1}
    )
    if not doc:
        raise NotFoundException(_NOT_FOUND)
    return current_user


async def verify_assessments_in_workspace(
    db: AsyncIOMotorDatabase, workspace_id: str, assessment_ids: list[str]
) -> list[ObjectId]:
    """Return the ObjectIds of ``assessment_ids`` once every one is verified to be an active
    assessment of ``workspace_id``.

    For routes that take several assessments at once, where the single-id ``AssessmentGuard``
    cannot be used. All-or-nothing: one malformed, foreign, missing or soft-deleted id fails the
    whole request with the same 404, so nothing is revealed about other tenants' objects.
    Order is kept (duplicates dropped).
    """
    oids = list(dict.fromkeys(to_oid(a) for a in assessment_ids))
    if not oids:
        raise NotFoundException(_NOT_FOUND)
    found = await db.assessments.count_documents(
        {"_id": {"$in": oids}, "workspace_id": to_oid(workspace_id), "is_active": {"$ne": False}}
    )
    if found != len(oids):
        raise NotFoundException(_NOT_FOUND)
    return oids


async def submission_access(
    workspace_id: str, assessment_id: str, submission_id: str, db: DB, current_user: AdminUser
) -> dict:
    await assessment_access(workspace_id, assessment_id, db, current_user)
    doc = await db.assessment_submissions.find_one(
        {"_id": to_oid(submission_id), "assessment_id": to_oid(assessment_id)}, {"_id": 1}
    )
    if not doc:
        raise NotFoundException(_NOT_FOUND)
    return current_user


async def share_access(
    workspace_id: str, assessment_id: str, share_id: str, db: DB, current_user: AdminUser
) -> dict:
    await assessment_access(workspace_id, assessment_id, db, current_user)
    doc = await db.assessment_shares.find_one(
        {
            "_id": to_oid(share_id),
            "assessment_id": to_oid(assessment_id),
            "workspace_id": to_oid(workspace_id),
        },
        {"_id": 1},
    )
    if not doc:
        raise NotFoundException(_NOT_FOUND)
    return current_user


async def candidate_access(
    workspace_id: str, assessment_id: str, candidate_id: str, db: DB, current_user: AdminUser
) -> dict:
    await assessment_access(workspace_id, assessment_id, db, current_user)
    to_oid(candidate_id)
    return current_user


async def submission_workspace_id(db: AsyncIOMotorDatabase, submission_id: str) -> str | None:
    """Workspace that owns a submission (denormalised, falling back to its assessment)."""
    sub = await db.assessment_submissions.find_one(
        {"_id": to_oid(submission_id)}, {"workspace_id": 1, "assessment_id": 1}
    )
    if not sub:
        return None
    if sub.get("workspace_id"):
        return str(sub["workspace_id"])
    asm = await db.assessments.find_one({"_id": sub["assessment_id"]}, {"workspace_id": 1})
    return str(asm["workspace_id"]) if asm and asm.get("workspace_id") else None


WorkspaceGuard = Depends(workspace_access)
AssessmentGuard = Depends(assessment_access)
SubmissionGuard = Depends(submission_access)
ShareGuard = Depends(share_access)
CandidateGuard = Depends(candidate_access)

__all__ = [
    "Annotated",
    "AssessmentGuard",
    "CandidateGuard",
    "ShareGuard",
    "SubmissionGuard",
    "WorkspaceGuard",
    "can_access_workspace",
    "is_super_admin",
    "member_workspace_ids",
    "submission_workspace_id",
    "to_oid",
    "verify_assessments_in_workspace",
]
