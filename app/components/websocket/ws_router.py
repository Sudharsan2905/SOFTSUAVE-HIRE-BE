"""WebSocket endpoint for live candidate interview sessions.

Connection URL:
    WS /api/ws/interview/{submission_id}?token={access_token}

Token is sent as query param because browsers cannot set custom headers on WebSocket
upgrade requests.

Message protocol
─────────────────
Client → Server:
  { "type": "ping", "remaining_seconds": <int>, "current_question_idx": <int> }

Server → Client:
  { "type": "connected", "status": "in_progress", "remaining_seconds": <int|null>,
    "current_question_idx": <int> }
  { "type": "on_hold",         "message": "Interview paused. Awaiting admin approval." }
  { "type": "resume_approved", "remaining_seconds": <int|null>, "current_question_idx": <int> }
  { "type": "terminated",      "message": "Session ended by administrator." }
  { "type": "pong" }
  { "type": "error",           "message": <str> }
"""

from datetime import UTC, datetime

from bson import ObjectId
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.common.constants.app_constants import SubmissionStatus
from app.components.websocket.connection_manager import manager
from app.core.logging import logger

ws_router = APIRouter()

_TERMINAL_STATUSES = {
    SubmissionStatus.COMPLETED,
    SubmissionStatus.MALPRACTICE,
    SubmissionStatus.TERMINATED,
}


async def _decode_token(token: str | None) -> dict | None:
    """Return JWT payload or None if invalid/missing."""
    if not token:
        return None
    try:
        from app.components.auth.auth_service import decode_access_token

        return decode_access_token(token)
    except Exception:
        return None


@ws_router.websocket("/ws/interview/{submission_id}")
async def interview_websocket(websocket: WebSocket, submission_id: str) -> None:
    token = websocket.query_params.get("token")
    payload = await _decode_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    candidate_id = payload.get("sub")
    if not candidate_id:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    db = websocket.app.state.db

    # Verify the submission belongs to this candidate
    try:
        sub = await db.assessment_submissions.find_one(
            {"_id": ObjectId(submission_id), "candidate_id": ObjectId(candidate_id)}
        )
    except Exception:
        await websocket.close(code=4004, reason="Invalid submission id")
        return

    if not sub:
        await websocket.close(code=4004, reason="Submission not found")
        return

    status = sub.get("status")
    if status in _TERMINAL_STATUSES:
        await websocket.close(code=4003, reason="Session already ended")
        return

    # Accept + register (also cancels any pending ON_HOLD task)
    await manager.connect(submission_id, websocket)

    # Send initial state
    if status == SubmissionStatus.ON_HOLD:
        await websocket.send_json(
            {
                "type": "on_hold",
                "message": "Interview paused. Awaiting admin approval to resume.",
            }
        )
    else:
        await websocket.send_json(
            {
                "type": "connected",
                "status": str(status),
                "remaining_seconds": sub.get("remaining_seconds"),
                "current_question_idx": sub.get("current_question_idx", 0),
            }
        )

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ping":
                remaining = data.get("remaining_seconds")
                q_idx = int(data.get("current_question_idx", 0))
                if remaining is not None:
                    await db.assessment_submissions.update_one(
                        {"_id": ObjectId(submission_id)},
                        {
                            "$set": {
                                "remaining_seconds": int(remaining),
                                "current_question_idx": q_idx,
                                "last_heartbeat": datetime.now(UTC),
                                "updated_at": datetime.now(UTC),
                            }
                        },
                    )
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("WS disconnect: submission_id=%s", submission_id)
        await manager.disconnect(submission_id, db)
    except Exception as exc:
        logger.error("WS error: submission_id=%s error=%s", submission_id, exc)
        await manager.disconnect(submission_id, db)
