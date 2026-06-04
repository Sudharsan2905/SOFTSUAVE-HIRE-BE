"""WebSocket connection manager for interview sessions.

Tracks active candidate connections per submission_id.
Designed to support 1200+ concurrent sessions via asyncio lightweight coroutines.

Scaling note: For multi-node deployments replace the in-memory dicts with a
Redis-backed implementation (e.g. redis-py asyncio + pub/sub channels).
The public interface is identical — only _send and _hold_task storage need
Redis-backed equivalents.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import WebSocket

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Seconds after disconnect before the session is placed ON_HOLD
HOLD_DELAY_SECONDS = 60


class ConnectionManager:
    def __init__(self) -> None:
        # submission_id → active WebSocket
        self._connections: dict[str, WebSocket] = {}
        # submission_id → asyncio Task that marks ON_HOLD after delay
        self._hold_tasks: dict[str, asyncio.Task] = {}

    async def connect(self, submission_id: str, websocket: WebSocket) -> None:
        """Accept a new WebSocket; cancel any pending ON_HOLD task for this session."""
        await websocket.accept()
        self._connections[submission_id] = websocket
        await self._cancel_hold_task(submission_id)
        logger.info(
            "WS connected: submission_id=%s total=%d", submission_id, len(self._connections)
        )

    async def disconnect(self, submission_id: str, db: "AsyncIOMotorDatabase") -> None:
        """Unregister a connection and schedule ON_HOLD transition after HOLD_DELAY_SECONDS."""
        self._connections.pop(submission_id, None)
        logger.info(
            "WS disconnected: submission_id=%s — ON_HOLD in %ds",
            submission_id,
            HOLD_DELAY_SECONDS,
        )
        task = asyncio.create_task(
            self._put_on_hold_after_delay(submission_id, db),
            name=f"hold-{submission_id}",
        )
        self._hold_tasks[submission_id] = task

    async def send_json(self, submission_id: str, data: dict) -> bool:
        """Push a JSON message to the connected candidate. Returns False if offline."""
        ws = self._connections.get(submission_id)
        if not ws:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception:
            self._connections.pop(submission_id, None)
            return False

    def is_connected(self, submission_id: str) -> bool:
        return submission_id in self._connections

    def active_count(self) -> int:
        return len(self._connections)

    # ── private ───────────────────────────────────────────────────────────────

    async def _cancel_hold_task(self, submission_id: str) -> None:
        task = self._hold_tasks.pop(submission_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _put_on_hold_after_delay(
        self,
        submission_id: str,
        db: "AsyncIOMotorDatabase",
    ) -> None:
        try:
            await asyncio.sleep(HOLD_DELAY_SECONDS)
        except asyncio.CancelledError:
            logger.debug("ON_HOLD cancelled (reconnected): submission_id=%s", submission_id)
            return

        if submission_id not in self._connections:
            from app.components.candidate.candidate_service import put_session_on_hold

            await put_session_on_hold(db, submission_id)

        self._hold_tasks.pop(submission_id, None)


# Module-level singleton — suitable for single-process (single uvicorn worker).
# Replace with Redis-backed manager for multi-worker / multi-node deployments.
manager = ConnectionManager()


class AdminConnectionManager:
    """Manages admin WebSocket connections for real-time monitoring events."""

    def __init__(self) -> None:
        # admin_id → websocket
        self._connections: dict[str, WebSocket] = {}
        # admin_id → list of workspace_ids they can see (empty = all)
        self._workspace_filters: dict[str, list[str]] = {}

    def connect(self, admin_id: str, websocket: WebSocket, workspace_ids: list[str]) -> None:
        self._connections[admin_id] = websocket
        self._workspace_filters[admin_id] = workspace_ids
        logger.info("Admin WS connected: admin_id=%s", admin_id)

    def disconnect(self, admin_id: str) -> None:
        self._connections.pop(admin_id, None)
        self._workspace_filters.pop(admin_id, None)

    async def broadcast_event(self, event: dict, workspace_id: str) -> None:
        """Send event to all admins who have access to workspace_id."""
        payload = {**event, "workspace_id": workspace_id}
        disconnected = []
        for admin_id, ws in self._connections.items():
            filters = self._workspace_filters.get(admin_id, [])
            if filters and workspace_id not in filters:
                continue  # admin doesn't have access to this workspace
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(admin_id)
        for aid in disconnected:
            self._connections.pop(aid, None)
            self._workspace_filters.pop(aid, None)


admin_manager = AdminConnectionManager()
