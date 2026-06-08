"""LiveKit token generation for screen-share monitoring.

Room strategy: one room per workspace (workspace-{workspace_id}).
Candidates publish screen track only. Admins subscribe to selected candidate.
"""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.exceptions import NotFoundException
from app.core.config import settings
from app.core.logging import logger


async def _get_workspace_id_for_submission(
    db: AsyncIOMotorDatabase,
    submission_id: str,
    candidate_id: str | None = None,
) -> str:
    """Resolve the workspace_id for a submission via its assessment.

    If candidate_id is provided, verifies the submission belongs to that candidate.
    """
    query: dict = {"_id": ObjectId(submission_id)}
    if candidate_id:
        query["candidate_id"] = ObjectId(candidate_id)
    sub = await db.assessment_submissions.find_one(query)
    if not sub:
        raise NotFoundException("Submission not found")
    assessment = await db.assessments.find_one({"_id": sub["assessment_id"], "is_active": True})
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

        token = (
            AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
            .with_identity(identity)
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=can_publish,
                    can_subscribe=can_subscribe,
                    can_publish_data=False,
                )
            )
        )
        jwt: str = token.to_jwt()
        logger.info(
            "LiveKit token generated: identity=%s room=%s publish=%s subscribe=%s",
            identity,
            room,
            can_publish,
            can_subscribe,
        )
        return jwt
    except ImportError:
        logger.warning("LiveKit package not installed — pip install livekit")
        return ""
    except Exception as exc:
        logger.error("LiveKit token generation failed: identity=%s error=%s", identity, exc)
        return ""


async def generate_candidate_token(
    db: AsyncIOMotorDatabase, submission_id: str, candidate_id: str
) -> dict:
    """Generate a LiveKit token for a candidate to publish their screen track.

    Raises NotFoundException if the submission does not belong to the candidate.
    """
    workspace_id = await _get_workspace_id_for_submission(db, submission_id, candidate_id)
    room = f"workspace-{workspace_id}"
    token = _make_token(
        identity=f"candidate-{submission_id}",
        room=room,
        can_publish=True,
        can_subscribe=False,
    )
    return {"token": token, "room": room, "workspace_id": workspace_id}


def generate_admin_token(admin_id: str, workspace_id: str) -> dict:
    """Generate a LiveKit token for admin to subscribe to screen tracks."""
    room = f"workspace-{workspace_id}"
    token = _make_token(
        identity=f"admin-{admin_id}",
        room=room,
        can_publish=False,
        can_subscribe=True,
    )
    return {"token": token, "room": room}
