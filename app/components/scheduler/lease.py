"""Tiny Mongo-backed lease so periodic jobs run on ONE worker per cycle.

With several uvicorn workers every process runs the reaper loop; whoever grabs the lease
for a cycle does the work, the others skip it.  Correctness never depends on the lease —
all state transitions are atomic claims — it only avoids N workers doing the same sweep.
"""

import os
import socket
import uuid
from datetime import timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.common.utils import utcnow

OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


async def acquire_lease(db: AsyncIOMotorDatabase, name: str, ttl_seconds: float) -> bool:
    """Try to take (or renew) the lease ``name`` for ``ttl_seconds``.

    Returns False only when another live owner holds it; database errors propagate."""
    now = utcnow()
    try:
        doc = await db.scheduler_leases.find_one_and_update(
            {"_id": name, "$or": [{"owner": OWNER}, {"expires_at": {"$lt": now}}]},
            {"$set": {"owner": OWNER, "expires_at": now + timedelta(seconds=ttl_seconds)}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return False  # another live owner holds it (the upsert collided on _id)
    # Any other error (database unreachable...) propagates: that is an infrastructure
    # failure, not "someone else holds the lease", and the job must exit non-zero.
    return bool(doc and doc.get("owner") == OWNER)


async def release_lease(db: AsyncIOMotorDatabase, name: str) -> None:
    """Release the lease early if we still own it (so the next run is not blocked until
    expiry).  Never raises — an unreleased lease simply expires."""
    try:
        await db.scheduler_leases.delete_one({"_id": name, "owner": OWNER})
    except Exception:  # noqa: S110
        pass
