"""Redis plumbing that lets several uvicorn workers / instances share WebSocket state.

Sockets themselves always live in the process that accepted them.  Redis provides:

* **presence** – ``ws:loc:<submission_id>`` = id of the instance holding that candidate's
  socket, with a TTL refreshed on every ping.  It answers "who has this candidate" for
  cross-worker pushes and "is this candidate connected anywhere" for ON_HOLD decisions, and
  it expires on its own when a worker dies (no in-memory timers needed).
* **pub/sub** – one channel per instance (``ws:inst:<instance_id>``) for targeted
  candidate pushes / close requests, and one shared channel (``ws:admin``) fanned out to
  every instance for admin monitoring broadcasts.

When ``settings.REDIS_URL`` is empty none of this is used and behaviour is unchanged.
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.core.logging import logger

_LOC_PREFIX = "ws:loc:"
_ADMIN_CHANNEL = "ws:admin"

# Refresh the TTL only while we still own the key.
_LUA_TOUCH = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
# Delete only while we still own the key (a newer connection on another instance may own it).
_LUA_CLEAR = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

Handler = Callable[[dict], Awaitable[None]]


class RedisBus:
    def __init__(self, url: str, client: Any = None) -> None:
        self.url = url
        self.instance_id = uuid.uuid4().hex[:12]
        self._client = client
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._on_instance: Handler | None = None
        self._on_admin: Handler | None = None
        self._touch: Any = None
        self._clear: Any = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def client(self) -> Any:
        if self._client is None:
            import redis.asyncio as aioredis

            # Bounded, blocking pool: a reconnect storm queues briefly instead of opening
            # thousands of sockets (or failing with "too many connections").
            pool = aioredis.BlockingConnectionPool.from_url(
                self.url,
                decode_responses=True,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                timeout=5,
                health_check_interval=30,
            )
            self._client = aioredis.Redis(connection_pool=pool)
        return self._client

    def register(self, on_instance: Handler | None = None, on_admin: Handler | None = None) -> None:
        if on_instance:
            self._on_instance = on_instance
        if on_admin:
            self._on_admin = on_admin

    async def start(self) -> None:
        """Start the pub/sub listener (idempotent)."""
        if self._task is not None and not self._task.done():
            return
        async with self._lock:
            if self._task is None or self._task.done():
                self._touch = self.client.register_script(_LUA_TOUCH)
                self._clear = self.client.register_script(_LUA_CLEAR)
                self._task = asyncio.create_task(self._listen(), name=f"ws-bus-{self.instance_id}")
                await asyncio.sleep(0)  # let it subscribe

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None

    # ── presence ─────────────────────────────────────────────────────────────

    async def set_presence(self, submission_id: str) -> None:
        await self.client.set(
            _LOC_PREFIX + submission_id, self.instance_id, ex=settings.WS_PRESENCE_TTL_SECONDS
        )

    async def touch(self, submission_id: str) -> None:
        if self._touch is None:
            await self.start()
        assert self._touch is not None  # noqa: S101 — set by start()
        refreshed = await self._touch(
            keys=[_LOC_PREFIX + submission_id],
            args=[self.instance_id, settings.WS_PRESENCE_TTL_SECONDS],
        )
        if not refreshed:
            # Key expired (e.g. Redis restart) while the socket is alive — re-assert it.
            await self.client.set(
                _LOC_PREFIX + submission_id,
                self.instance_id,
                ex=settings.WS_PRESENCE_TTL_SECONDS,
                nx=True,
            )

    async def clear_presence(self, submission_id: str) -> None:
        if self._clear is None:
            await self.start()
        assert self._clear is not None  # noqa: S101 — set by start()
        await self._clear(keys=[_LOC_PREFIX + submission_id], args=[self.instance_id])

    async def locate(self, submission_id: str) -> str | None:
        value = await self.client.get(_LOC_PREFIX + submission_id)
        return str(value) if value else None

    # ── pub/sub ──────────────────────────────────────────────────────────────

    def _instance_channel(self, instance_id: str) -> str:
        return f"ws:inst:{instance_id}"

    async def publish_instance(self, instance_id: str, message: dict) -> int:
        """Send to one instance. Returns the number of subscribers that received it."""
        return int(
            await self.client.publish(
                self._instance_channel(instance_id), json.dumps(message, default=str)
            )
        )

    async def publish_admin(self, message: dict) -> None:
        await self.client.publish(_ADMIN_CHANNEL, json.dumps(message, default=str))

    async def _listen(self) -> None:
        backoff = 0.5
        while True:
            pubsub = self.client.pubsub()
            try:
                await pubsub.subscribe(self._instance_channel(self.instance_id), _ADMIN_CHANNEL)
                backoff = 0.5
                async for item in pubsub.listen():
                    if item.get("type") != "message":
                        continue
                    await self._dispatch(item["channel"], item["data"])
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
                raise
            except Exception as exc:
                logger.warning("WS bus listener error (reconnecting): {}", exc)
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)

    async def _dispatch(self, channel: str, raw: str) -> None:
        try:
            message = json.loads(raw)
            handler = self._on_admin if channel == _ADMIN_CHANNEL else self._on_instance
            if handler and isinstance(message, dict):
                await handler(message)
        except Exception as exc:  # one bad message must not kill the listener
            logger.warning("WS bus dispatch failed: {}", exc)


_bus: RedisBus | None = None


def get_bus() -> RedisBus | None:
    """The process-wide bus, or None when Redis is not configured."""
    global _bus
    url = settings.REDIS_URL
    if not url:
        return None
    if _bus is None or _bus.url != url:
        _bus = RedisBus(url)
    return _bus


async def shutdown_bus() -> None:
    global _bus
    if _bus is not None:
        await _bus.stop()
        _bus = None
