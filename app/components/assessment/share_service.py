"""Share-link service for assessments.

Provides reusable, non-candidate-specific share links for assessments.
Two share types are supported:

  - expirable: time-bounded link encoded with start_time and end_time
  - custom:    permanent-style decodable link with a random nonce so multiple
               custom shares can coexist for the same assessment
"""

import secrets
from datetime import UTC
from datetime import datetime as _dt

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.exceptions import NotFoundException, ValidationException
from app.common.utils import (
    encode_custom_sharelink,
    encode_expirable_sharelink,
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

    Generates a share link whose type is determined by ``data["share_type"]``:

    * ``"expirable"`` — time-bounded link; requires ``start_time`` and
      ``end_time`` in ISO 8601 format with ``start_time < end_time``.
    * ``"custom"``    — permanent-style link with a custom prefix so it can
      be distinguished from the main assessment share link at decode time.

    Args:
        db:            Motor database instance.
        assessment_id: The assessment to share.
        workspace_id:  Workspace that owns the assessment.
        data:          Validated request dict with keys:
                       share_type, label, monitoring_overrides,
                       start_time (optional), end_time (optional).
        created_by:    User ID of the admin creating the share.

    Returns:
        Serialised assessment_shares document.

    Raises:
        NotFoundException:  If the assessment does not exist in the workspace.
        ValidationException: If time validation fails for expirable links.
    """
    assessment = await db.assessments.find_one(
        {"_id": ObjectId(assessment_id), "workspace_id": ObjectId(workspace_id)}
    )
    if not assessment:
        raise NotFoundException(_ERR_ASSESSMENT_NOT_FOUND)

    share_type: str = data["share_type"]
    start_time: str | None = data.get("start_time")
    end_time: str | None = data.get("end_time")

    if share_type == "expirable":
        # Both timestamps are required and start must precede end.
        if not start_time or not end_time:
            raise ValidationException(
                "start_time and end_time are required for expirable share links"
            )
        try:
            s = _dt.fromisoformat(start_time).replace(tzinfo=UTC)
            e = _dt.fromisoformat(end_time).replace(tzinfo=UTC)
        except ValueError as err:
            raise ValidationException("Invalid date format. Use ISO 8601.") from err
        if e <= s:
            raise ValidationException("end_time must be after start_time")

        share_link = encode_expirable_sharelink(assessment_id, start_time, end_time)

    elif share_type == "custom":
        # Each custom link gets a random nonce so multiple shares for the same
        # assessment produce distinct, decodable links.
        nonce = secrets.token_urlsafe(8)
        share_link = encode_custom_sharelink(assessment_id, nonce)

    else:
        raise ValidationException(f"Unknown share_type: {share_type!r}")

    now = utcnow()
    doc: dict = {
        "assessment_id": ObjectId(assessment_id),
        "workspace_id": ObjectId(workspace_id),
        "share_type": share_type,
        "label": data.get("label"),
        "monitoring_overrides": data.get("monitoring_overrides"),
        "share_link": share_link,
        "start_time": start_time,
        "end_time": end_time,
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
