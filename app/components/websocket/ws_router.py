"""WebSocket endpoints for live candidate interview sessions and admin monitoring.

Connection URLs:
    WS /api/ws/interview/{submission_id}?token={access_token}
    WS /api/ws/admin?token={access_token}

Token is sent as query param because browsers cannot set custom headers on WebSocket
upgrade requests.

Message protocol — candidate endpoint
──────────────────────────────────────
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

Message protocol — admin endpoint
───────────────────────────────────
Client → Server:
  { "type": "ping" }

Server → Client:
  { "type": "connected", "role": <str> }
  { "type": "pong" }
  (broadcast events via AdminConnectionManager.broadcast_event)
"""

from datetime import UTC, datetime

from bson import ObjectId
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.common.constants.app_constants import SubmissionStatus
from app.components.websocket.connection_manager import admin_manager, manager
from app.core.logging import logger

ws_router = APIRouter()

_TERMINAL_STATUSES = {
    SubmissionStatus.COMPLETED,
    SubmissionStatus.TERMINATED,
    SubmissionStatus.MALPRACTICE,
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


@ws_router.websocket("/ws/admin")
async def admin_websocket(websocket: WebSocket) -> None:
    """Global admin WebSocket for real-time submission monitoring events.

    Delivers status changes and malpractice events filtered to workspaces
    the admin has access to. Super admins receive events from all workspaces.

    Auth: JWT passed as ?token= query param.
    """
    token = websocket.query_params.get("token")
    payload = await _decode_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    admin_id: str | None = payload.get("sub")
    if not admin_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    role = payload.get("role", "")
    if role not in ("admin", "super_admin"):
        await websocket.close(code=4003, reason="Admin access required")
        return

    db = websocket.app.state.db

    # Resolve accessible workspace IDs
    workspace_ids: list[str] = []
    if role == "super_admin":
        workspace_ids = []  # empty = all workspaces
    else:
        user = await db.users.find_one({"_id": ObjectId(admin_id)})
        workspace_ids = user.get("workspace_ids", []) if user else []

    await websocket.accept()
    await websocket.send_json({"type": "connected", "role": role})

    # Register in admin connection manager
    admin_manager.connect(admin_id, websocket, workspace_ids)

    try:
        while True:
            data = await websocket.receive_json()
            await _handle_admin_message(websocket, data)
    except WebSocketDisconnect:
        admin_manager.disconnect(admin_id)
    except Exception as exc:
        logger.error("Admin WS error: admin_id=%s error=%s", admin_id, exc)
        admin_manager.disconnect(admin_id)


async def _handle_admin_message(websocket: WebSocket, data: dict) -> None:
    """Dispatch a single admin WebSocket message."""
    msg_type = data.get("type")

    if msg_type == "ping":
        await websocket.send_json({"type": "pong"})
        return

    if msg_type == "warn_candidate":
        target_submission_id = data.get("submission_id", "")
        message = data.get("message", "").strip()
        if not target_submission_id or not message:
            return
        delivered = await manager.send_json(
            target_submission_id,
            {"type": "admin_warning", "message": message},
        )
        await websocket.send_json(
            {
                "type": "warn_ack",
                "submission_id": target_submission_id,
                "delivered": delivered,
            }
        )
