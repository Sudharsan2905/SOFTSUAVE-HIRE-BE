"""Tiny in-process TTL cache for the authenticated user document.

Every authenticated request used to hit ``users.find_one`` — about a third of all database
operations on the exam hot path (autosave, round fetch, screenshots...).  Caching the user
for a few seconds removes that round-trip while keeping deactivation / role changes
effective almost immediately (writes in this process invalidate the entry at once; other
processes see the change after the TTL).
"""

import time
from typing import Any

from app.core.config import settings

_MAX_ENTRIES = 10_000
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def get(user_id: str) -> dict[str, Any] | None:
    ttl = settings.USER_CACHE_TTL_SECONDS
    if ttl <= 0:
        return None
    hit = _cache.get(user_id)
    if hit is None:
        return None
    stored_at, doc = hit
    if time.monotonic() - stored_at > ttl:
        _cache.pop(user_id, None)
        return None
    return dict(doc)


def put(user_id: str, doc: dict[str, Any]) -> None:
    if settings.USER_CACHE_TTL_SECONDS <= 0:
        return
    if len(_cache) >= _MAX_ENTRIES:
        _cache.clear()
    _cache[user_id] = (time.monotonic(), dict(doc))


def invalidate(user_id: str | None = None) -> None:
    """Drop one user (or everything when ``user_id`` is None)."""
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(str(user_id), None)
