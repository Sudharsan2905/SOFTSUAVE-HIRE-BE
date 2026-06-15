"""Share-link service for assessments.

Each share link is uniquely encoded with a random nonce and optionally scoped
to a time window (start_time / end_time). Both permanent and time-bounded
shares are stored in assessment_shares and resolved at validation time.
"""

import secrets
from datetime import UTC
from datetime import datetime as _dt

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.exceptions import NotFoundException, ValidationException
from app.common.utils import (
    encode_sharelink,
    serialize_doc,
    serialize_docs,
    utcnow,
)

_ERR_ASSESSMENT_NOT_FOUND = "Assessment not found"
_ERR_SHARE_NOT_FOUND = "Share not found"


async def create_share(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    workspace_id: str,
    data: dict,
    created_by: str,
) -> dict:
    """Create a new share link for an assessment.

    The link is always encoded with a random nonce for per-document uniqueness.
    Providing ``start_time`` and/or ``end_time`` makes it time-bounded:

    * Both provided           → time-bounded; start must precede end.
    * Only ``end_time``       → start defaults to the current UTC time.
    * Neither                 → permanent share (no time restriction).

    Args:
        db:            Motor database instance.
        assessment_id: The assessment to share.
        workspace_id:  Workspace that owns the assessment.
        data:          Validated request dict with keys:
                       label, monitoring_overrides,
                       start_time (optional), end_time (optional).
        created_by:    User ID of the admin creating the share.

    Returns:
        Serialised assessment_shares document.

    Raises:
        NotFoundException:   If the assessment does not exist in the workspace.
        ValidationException: If time validation fails.
    """
    assessment = await db.assessments.find_one(
        {"_id": ObjectId(assessment_id), "workspace_id": ObjectId(workspace_id), "is_active": True}
    )
    if not assessment:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    start_time: str | None = data.get("start_time")
    end_time: str | None = data.get("end_time")

    # end_time without start_time → default start to now
    if end_time and not start_time:
        start_time = utcnow().isoformat()

    if start_time and end_time:
        try:
            s = _dt.fromisoformat(start_time).replace(tzinfo=UTC)
            e = _dt.fromisoformat(end_time).replace(tzinfo=UTC)
        except ValueError as err:
            raise ValidationException("Invalid date format. Use ISO 8601.") from err
        if e <= s:
            raise ValidationException("end_time must be after start_time")

    nonce = secrets.token_urlsafe(8)
    share_link = encode_sharelink(assessment_id, nonce, start_time, end_time)

    now = utcnow()
    doc: dict = {
        "assessment_id": ObjectId(assessment_id),
        "workspace_id": ObjectId(workspace_id),
        "label": data["label"],
        "monitoring_overrides": data.get("monitoring_overrides"),
        "share_link": share_link,
        "start_time": start_time,
        "end_time": end_time,
        "restrict_candidate_access": data.get("restrict_candidate_access", False),
        "restriction_mode": data.get("restriction_mode"),
        "restricted_emails": data.get("restricted_emails", []),
        "is_active": True,
        "created_by": ObjectId(created_by),
        "created_at": now,
        "updated_at": now,
    }

    result = await db.assessment_shares.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_doc(doc)


async def get_shares(
    db: AsyncIOMotorDatabase,
    assessment_id: str,
    workspace_id: str,
) -> list:
    """Return all active share links for an assessment.

    Args:
        db:            Motor database instance.
        assessment_id: Assessment to query.
        workspace_id:  Workspace that owns the assessment.

    Returns:
        List of serialised assessment_shares documents (active only).
    """
    docs = (
        await db.assessment_shares.find(
            {
                "assessment_id": ObjectId(assessment_id),
                "workspace_id": ObjectId(workspace_id),
                "is_active": True,
            }
        )
        .sort("created_at", -1)
        .to_list(1000)
    )
    return serialize_docs(docs)


async def delete_share(
    db: AsyncIOMotorDatabase,
    share_id: str,
    workspace_id: str,
) -> None:
    """Soft-delete a share link by setting is_active=False.

    Args:
        db:           Motor database instance.
        share_id:     The _id of the share to deactivate.
        workspace_id: Workspace guard — prevents cross-workspace deletion.

    Raises:
        NotFoundException: If no active share with that ID exists in the workspace.
    """
    result = await db.assessment_shares.update_one(
        {
            "_id": ObjectId(share_id),
            "workspace_id": ObjectId(workspace_id),
            "is_active": True,
        },
        {"$set": {"is_active": False, "updated_at": utcnow()}},
    )
    if result.matched_count == 0:
        raise NotFoundException(_ERR_SHARE_NOT_FOUND)


async def get_share_by_link(
    db: AsyncIOMotorDatabase,
    share_link: str,
) -> dict | None:
    """Look up an active share document by its share_link value.

    Args:
        db:         Motor database instance.
        share_link: The encoded share link string to search for.

    Returns:
        Serialised share document, or ``None`` if not found or not active.
    """
    doc = await db.assessment_shares.find_one({"share_link": share_link, "is_active": True})
    if not doc:
        return None
    return serialize_doc(doc)
