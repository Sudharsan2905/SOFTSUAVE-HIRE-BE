"""WebSocket endpoints for live candidate interview sessions and admin monitoring.

Connection URLs:
    WS /api/ws/interview/{submission_id}?token={access_token}
    WS /api/ws/admin?token={access_token}

Token is sent as query param because browsers cannot set custom headers on WebSocket
upgrade requests.

Message protocol — candidate endpoint
──────────────────────────────────────
Client → Server:
  { "type": "ping", "current_question_idx": <int> }
  (remaining time is owned by the server; any client-supplied value is ignored)

Server → Client:
  { "type": "connected", "status": "in_progress", "remaining_seconds": <int|null>,
    "current_question_idx": <int> }
  { "type": "on_hold",         "message": "Interview paused. Awaiting admin approval." }
  { "type": "resume_approved", "remaining_seconds": <int|null>, "current_question_idx": <int> }
  { "type": "terminated",      "message": "Session ended by administrator." }
  { "type": "pong", "remaining_seconds": <int>, "status": <str> }
  { "type": "time_expired" }   (the server auto-submitted the session)
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

import asyncio
import contextlib
import time
from datetime import UTC, datetime

from bson import ObjectId
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.common.constants.app_constants import SubmissionStatus
from app.components.candidate import timing
from app.components.websocket.connection_manager import admin_manager, manager
from app.core.config import settings
from app.core.logging import logger

ws_router = APIRouter()


class _PingState:
    """Per-connection cache so most heartbeats are answered without touching the database.

    The deadline is server-owned and changes only on events that are pushed over this very
    socket (resume, auto-submit), so the cached document stays correct between periodic
    resyncs; the DB is touched when the question index changes or every
    ``WS_PING_SYNC_SECONDS``.
    """

    __slots__ = ("last_sync", "q_idx", "sub")

    def __init__(self, sub: dict) -> None:
        self.sub = sub
        self.q_idx = int(sub.get("current_question_idx", 0) or 0)
        self.last_sync = time.monotonic()


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

    # Stamp liveness first so the stale-session sweep never sees an old heartbeat for a
    # candidate who is connecting right now.
    with contextlib.suppress(Exception):
        await db.assessment_submissions.update_one(
            {"_id": sub["_id"]}, {"$set": {"last_heartbeat": datetime.now(UTC)}}
        )

    # Accept + register (also cancels any pending ON_HOLD task)
    await manager.connect(submission_id, websocket)
    logger.info("WS connected: submission_id={} candidate_id={}", submission_id, candidate_id)

    # Send initial state
    if status == SubmissionStatus.ON_HOLD:
        logger.info("WS on_hold sent on connect: submission_id={}", submission_id)
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
                "remaining_seconds": timing.remaining_seconds(sub),
                "current_question_idx": sub.get("current_question_idx", 0),
            }
        )

    state = _PingState(sub)
    try:
        while True:
            # Idle timeout: clients ping every ~10s, so silence means a dead/half-open socket.
            data = await asyncio.wait_for(
                websocket.receive_json(), timeout=settings.WS_IDLE_TIMEOUT_SECONDS
            )
            if not isinstance(data, dict):
                continue
            if data.get("type") == "ping" and not await _handle_ping(
                websocket, db, submission_id, data, state
            ):
                break
    except WebSocketDisconnect:
        logger.info("WS disconnect: submission_id={}", submission_id)
    except TimeoutError:
        logger.info("WS idle timeout: submission_id={}", submission_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=4008, reason="Idle timeout")
    except Exception as exc:
        logger.error("WS error: submission_id={} error={}", submission_id, exc)
    finally:
        await manager.disconnect(submission_id, db, websocket)


async def _handle_ping(
    websocket: WebSocket,
    db: AsyncIOMotorDatabase,
    submission_id: str,
    data: dict,
    state: _PingState,
) -> bool:
    """Reply with the server-computed remaining time; persist position only when needed.

    Returns False when the session is no longer active (the socket is then closed).
    A transient DB error must not drop the session, so it is swallowed here.
    """
    await manager.touch(submission_id)  # refresh cross-worker presence (no-op without Redis)
    try:
        q_idx = max(0, int(data.get("current_question_idx", 0)))
    except (TypeError, ValueError):
        q_idx = 0

    due = time.monotonic() - state.last_sync >= settings.WS_PING_SYNC_SECONDS
    if q_idx != state.q_idx or due:
        try:
            sub = await db.assessment_submissions.find_one_and_update(
                {
                    "_id": ObjectId(submission_id),
                    "status": {"$in": [SubmissionStatus.IN_PROGRESS, SubmissionStatus.ON_HOLD]},
                },
                {"$set": {"current_question_idx": q_idx, "last_heartbeat": datetime.now(UTC)}},
                projection={
                    "status": 1,
                    "paused_at": 1,
                    "current_round": 1,
                    "current_question_idx": 1,
                    "current_deadline_at": 1,
                    "current_round_seconds": 1,
                    "round_timer_started": 1,
                    "rounds_data.round_number": 1,
                    "rounds_data.max_duration_minutes": 1,
                    "rounds_data.started_at": 1,
                },
                return_document=ReturnDocument.AFTER,
            )
        except Exception as exc:
            logger.warning("WS ping DB error: submission_id={} error={}", submission_id, exc)
            await websocket.send_json({"type": "pong"})
            return True

        if sub is None:
            # Completed / terminated / auto-submitted since the last sync.
            await websocket.send_json({"type": "terminated", "reason": "session_ended"})
            await websocket.close(code=4003, reason="Session already ended")
            return False
        state.sub = sub
        state.q_idx = q_idx
        state.last_sync = time.monotonic()

    await websocket.send_json(
        {
            "type": "pong",
            "remaining_seconds": timing.remaining_seconds(state.sub),
            "status": str(state.sub.get("status")),
        }
    )
    return True


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
        workspace_ids = []  # super admins are tracked separately and see everything
    else:
        user = await db.users.find_one({"_id": ObjectId(admin_id)})
        workspace_ids = user.get("workspace_ids", []) if user else []

    await websocket.accept()
    await websocket.send_json({"type": "connected", "role": role})

    # Register in admin connection manager
    admin_manager.connect(admin_id, websocket, workspace_ids, role == "super_admin")
    await admin_manager.ensure_bus()

    try:
        while True:
            data = await websocket.receive_json()
            if isinstance(data, dict):
                await _handle_admin_message(websocket, data, admin_id, db)
    except WebSocketDisconnect:
        admin_manager.disconnect(admin_id)
    except Exception as exc:
        logger.error("Admin WS error: admin_id={} error={}", admin_id, exc)
        admin_manager.disconnect(admin_id)


async def _handle_admin_message(
    websocket: WebSocket, data: dict, admin_id: str, db: AsyncIOMotorDatabase
) -> None:
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
        if not await _admin_may_access_submission(db, admin_id, target_submission_id):
            await websocket.send_json(
                {"type": "warn_ack", "submission_id": target_submission_id, "delivered": False}
            )
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


async def _admin_may_access_submission(
    db: AsyncIOMotorDatabase, admin_id: str, submission_id: str
) -> bool:
    """True if the admin belongs to the workspace that owns the submission."""
    try:
        sub = await db.assessment_submissions.find_one(
            {"_id": ObjectId(submission_id)}, {"workspace_id": 1, "assessment_id": 1}
        )
        if not sub:
            return False
        workspace_id = sub.get("workspace_id")
        if not workspace_id:  # legacy submission
            assessment = await db.assessments.find_one(
                {"_id": sub["assessment_id"]}, {"workspace_id": 1}
            )
            workspace_id = assessment.get("workspace_id") if assessment else None
        return bool(workspace_id) and admin_manager.can_access(admin_id, str(workspace_id))
    except Exception:
        return False
