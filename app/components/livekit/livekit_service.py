"""LiveKit token generation for screen-share monitoring.

Room strategy: one room per workspace (workspace-{workspace_id}).
Candidates publish screen track only. Admins subscribe to selected candidate.
"""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.exceptions import NotFoundException
from app.core.config import settings
from app.core.logging import logger


async def _get_workspace_id_for_submission(db: AsyncIOMotorDatabase, submission_id: str) -> str:
    """Resolve the workspace_id for a submission via its assessment."""
    sub = await db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
    if not sub:
        raise NotFoundException("Submission not found")
    assessment = await db.assessments.find_one({"_id": sub["assessment_id"]})
    if not assessment:
        raise NotFoundException("Assessment not found")
    return str(assessment["workspace_id"])


def _make_token(identity: str, room: str, can_publish: bool, can_subscribe: bool) -> str:
    """Generate a LiveKit access token. Returns empty string if LiveKit not configured."""
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        logger.warning("LiveKit not configured — LIVEKIT_API_KEY/SECRET missing")
        return ""

    try:
        from livekit.api import AccessToken, VideoGrants

        token = AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        token.identity = identity
        token.add_grants(
            VideoGrants(
                room_join=True,
                room=room,
                can_publish=can_publish,
                can_subscribe=can_subscribe,
                can_publish_data=False,
            )
        )
        return str(token.to_jwt())
    except ImportError:
        logger.warning("livekit package not installed — pip install livekit")
        return ""


async def generate_candidate_token(db: AsyncIOMotorDatabase, submission_id: str) -> dict:
    """Generate a LiveKit token for a candidate to publish their screen track."""
    workspace_id = await _get_workspace_id_for_submission(db, submission_id)
    room = f"workspace-{workspace_id}"
    token = _make_token(
        identity=f"candidate-{submission_id}",
        room=room,
        can_publish=True,
        can_subscribe=False,
    )
    return {"token": token, "room": room, "workspace_id": workspace_id}


async def generate_admin_token(admin_id: str, workspace_id: str) -> dict:
    """Generate a LiveKit token for admin to subscribe to screen tracks."""
    room = f"workspace-{workspace_id}"
    token = _make_token(
        identity=f"admin-{admin_id}",
        room=room,
        can_publish=False,
        can_subscribe=True,
    )
    return {"token": token, "room": room}
