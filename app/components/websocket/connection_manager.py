"""WebSocket connection manager for interview sessions.

Tracks active candidate connections per submission_id.
Designed to support 1200+ concurrent sessions via asyncio lightweight coroutines.

Multi-worker / multi-instance: set ``REDIS_URL``.  Sockets stay in the process that
accepted them; Redis (see ``redis_bus``) adds cross-worker presence and pub/sub so
terminate / time_expired / resume / admin-warning pushes and admin broadcasts reach a
socket wherever it lives.  With ``REDIS_URL`` empty everything is in-process, as before.
"""

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from fastapi import WebSocket

from app.components.websocket.redis_bus import RedisBus, get_bus
from app.core.config import settings

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # submission_id → active WebSocket
        self._connections: dict[str, WebSocket] = {}
        # submission_id → asyncio Task that marks ON_HOLD after delay
        self._hold_tasks: dict[str, asyncio.Task] = {}
        self._registered_bus: RedisBus | None = None
        # submission_id → monotonic time of the last presence refresh (throttle)
        self._last_touch: dict[str, float] = {}

    # ── Redis bus (optional) ─────────────────────────────────────────────────

    def _bus(self) -> RedisBus | None:
        bus = get_bus()
        if bus is not None and self._registered_bus is not bus:
            bus.register(on_instance=self._on_bus_message)
            self._registered_bus = bus
        return bus

    async def _bus_call(self, name: str, *args: object) -> object:
        """Run a bus operation; Redis trouble must never break the exam, so log and degrade."""
        bus = self._bus()
        if bus is None:
            return None
        try:
            await bus.start()
            return await getattr(bus, name)(*args)
        except Exception as exc:
            logger.warning("WS bus %s failed: %s", name, exc)
            return None

    async def _on_bus_message(self, message: dict) -> None:
        sid = str(message.get("sid", ""))
        op = message.get("op")
        if op == "send":
            await self._send_local(sid, message.get("data") or {})
        elif op == "close":
            ws = self._connections.pop(sid, None)
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.close(code=4009, reason="Replaced by a newer connection")

    async def touch(self, submission_id: str) -> None:
        """Refresh this candidate's presence TTL (called on every ping).

        Throttled to a third of the TTL per socket: pings arrive every ~10 s but the key
        lives ~45 s, so most heartbeats need no Redis round-trip at all.
        """
        if self._bus() is None:
            return
        now = time.monotonic()
        if now - self._last_touch.get(submission_id, 0.0) < settings.WS_PRESENCE_TTL_SECONDS / 3:
            return
        self._last_touch[submission_id] = now
        await self._bus_call("touch", submission_id)

    async def is_connected_anywhere(self, submission_id: str) -> bool:
        """True if the candidate has a live socket on this or (with Redis) any instance."""
        if submission_id in self._connections:
            return True
        located = await self._bus_call("locate", submission_id)
        return bool(located)

    async def connect(self, submission_id: str, websocket: WebSocket) -> None:
        """Accept a new WebSocket; cancel any pending ON_HOLD task for this session.

        A second connection for the same submission (another tab/device, or a reconnect
        that raced the old socket's close) replaces the first, which is closed.
        """
        await websocket.accept()
        previous = self._connections.get(submission_id)
        self._connections[submission_id] = websocket
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=4009, reason="Replaced by a newer connection")
            except Exception:  # noqa: S110 — already closed
                pass
        await self._cancel_hold_task(submission_id)
        if self._bus() is not None:
            previous_owner = await self._bus_call("locate", submission_id)
            bus = self._bus()
            if bus is not None and previous_owner and previous_owner != bus.instance_id:
                # The old socket is on another worker: ask it to drop it.
                await self._bus_call(
                    "publish_instance", previous_owner, {"op": "close", "sid": submission_id}
                )
            await self._bus_call("set_presence", submission_id)
            self._last_touch[submission_id] = time.monotonic()
        logger.info(
            "WS connected: submission_id=%s total=%d", submission_id, len(self._connections)
        )

    async def disconnect(
        self,
        submission_id: str,
        db: "AsyncIOMotorDatabase",
        websocket: WebSocket | None = None,
    ) -> None:
        """Unregister a connection and schedule ON_HOLD transition after delay.

        If ``websocket`` is given and it is no longer the registered socket (it was
        replaced by a newer one), nothing happens — the live session must not be held.
        """
        current = self._connections.get(submission_id)
        if websocket is not None and current is not websocket:
            return
        self._connections.pop(submission_id, None)
        self._last_touch.pop(submission_id, None)
        await self._bus_call("clear_presence", submission_id)
        logger.info(
            "WS disconnected: submission_id=%s — ON_HOLD in %ds",
            submission_id,
            settings.HOLD_DELAY_SECONDS,
        )
        await self._cancel_hold_task(submission_id)
        task = asyncio.create_task(
            self._put_on_hold_after_delay(submission_id, db),
            name=f"hold-{submission_id}",
        )
        self._hold_tasks[submission_id] = task

    async def _send_local(self, submission_id: str, data: dict) -> bool:
        ws = self._connections.get(submission_id)
        if not ws:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception:
            self._connections.pop(submission_id, None)
            return False

    async def send_json(self, submission_id: str, data: dict) -> bool:
        """Push a JSON message to the candidate wherever their socket lives.

        Local socket first; otherwise (Redis mode) forward to the owning instance.
        Returns False if the candidate is offline.
        """
        if await self._send_local(submission_id, data):
            return True
        bus = self._bus()
        if bus is None:
            return False
        owner = await self._bus_call("locate", submission_id)
        if not owner or owner == bus.instance_id:
            return False
        received = await self._bus_call(
            "publish_instance", owner, {"op": "send", "sid": submission_id, "data": data}
        )
        return bool(received)

    def is_connected(self, submission_id: str) -> bool:
        return submission_id in self._connections

    def active_count(self) -> int:
        return len(self._connections)

    # ── private ───────────────────────────────────────────────────────────────

    async def _cancel_hold_task(self, submission_id: str) -> None:
        task = self._hold_tasks.pop(submission_id, None)
        if task and not task.done() and task is not asyncio.current_task():
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
            await asyncio.sleep(settings.HOLD_DELAY_SECONDS)
        except asyncio.CancelledError:
            logger.debug("ON_HOLD cancelled (reconnected): submission_id=%s", submission_id)
            return

        if not await self.is_connected_anywhere(submission_id):
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
        self._super_admins: set[str] = set()
        self._registered_bus: RedisBus | None = None

    def _bus(self) -> RedisBus | None:
        bus = get_bus()
        if bus is not None and self._registered_bus is not bus:
            bus.register(on_admin=self._on_bus_message)
            self._registered_bus = bus
        return bus

    async def ensure_bus(self) -> None:
        """Register the admin handler and start the listener (call when an admin connects,
        so broadcasts raised on other workers reach this worker's admin sockets)."""
        bus = self._bus()
        if bus is None:
            return
        try:
            await bus.start()
        except Exception as exc:
            logger.warning("Admin WS bus start failed: %s", exc)

    async def _on_bus_message(self, message: dict) -> None:
        bus = self._bus()
        if bus is not None and message.get("origin") == bus.instance_id:
            return  # already delivered locally by the publisher
        await self._deliver_local(
            message.get("payload") or {}, str(message.get("workspace_id", ""))
        )

    def connect(
        self,
        admin_id: str,
        websocket: WebSocket,
        workspace_ids: list[str],
        is_super_admin: bool = False,
    ) -> None:
        self._connections[admin_id] = websocket
        self._workspace_filters[admin_id] = workspace_ids
        if is_super_admin:
            self._super_admins.add(admin_id)
        else:
            self._super_admins.discard(admin_id)
        logger.info("Admin WS connected: admin_id=%s", admin_id)

    def disconnect(self, admin_id: str) -> None:
        self._connections.pop(admin_id, None)
        self._workspace_filters.pop(admin_id, None)
        self._super_admins.discard(admin_id)

    def can_access(self, admin_id: str, workspace_id: str) -> bool:
        """Super admins see everything; others only their own workspaces (none → nothing)."""
        if admin_id in self._super_admins:
            return True
        return workspace_id in self._workspace_filters.get(admin_id, [])

    async def broadcast_event(self, event: dict, workspace_id: str) -> None:
        """Send event to all admins (on every worker) who have access to workspace_id."""
        payload = {**event, "workspace_id": workspace_id}
        await self._deliver_local(payload, workspace_id)
        bus = self._bus()
        if bus is not None:
            try:
                await bus.start()
                await bus.publish_admin(
                    {"origin": bus.instance_id, "workspace_id": workspace_id, "payload": payload}
                )
            except Exception as exc:
                logger.warning("Admin broadcast via Redis failed: %s", exc)

    async def _deliver_local(self, payload: dict, workspace_id: str) -> None:
        targets = [
            (admin_id, ws)
            for admin_id, ws in list(self._connections.items())
            if self.can_access(admin_id, workspace_id)
        ]

        async def _send(admin_id: str, ws: WebSocket) -> str | None:
            try:
                await ws.send_json(payload)
                return None
            except Exception:
                return admin_id

        failed = await asyncio.gather(*(_send(a, ws) for a, ws in targets))
        for aid in failed:
            if aid:
                self.disconnect(aid)


admin_manager = AdminConnectionManager()
