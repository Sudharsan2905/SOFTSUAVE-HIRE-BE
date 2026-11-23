"""One-off migration: lower-case and strip every stored user email.

Run once after deploying the email-normalisation change:

    python -m scripts.normalize_emails            # dry run (prints what would change)
    python -m scripts.normalize_emails --apply    # perform the update

Accounts whose canonical email would collide with another account are reported and left
untouched so an operator can merge them by hand.
"""

import asyncio
import sys

from motor.motor_asyncio import AsyncIOMotorClient

from app.common.utils import normalize_email
from app.core.config import settings


async def main(apply: bool) -> None:
    client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]
    seen: dict[str, object] = {}
    changes: list[tuple[object, str, str]] = []
    conflicts: list[tuple[object, str]] = []
    async for u in db.users.find({}, {"email": 1}):
        raw = u.get("email") or ""
        canon = normalize_email(raw)
        if canon in seen and seen[canon] != u["_id"]:
            conflicts.append((u["_id"], raw))
            continue
        seen[canon] = u["_id"]
        if canon != raw:
            changes.append((u["_id"], raw, canon))
    for _id, raw, canon in changes:
        print(f"{'UPDATE' if apply else 'would update'} {_id}: {raw!r} -> {canon!r}")
        if apply:
            await db.users.update_one({"_id": _id}, {"$set": {"email": canon}})
    for _id, raw in conflicts:
        print(f"CONFLICT (left unchanged) {_id}: {raw!r}")
    print(f"{len(changes)} change(s), {len(conflicts)} conflict(s), apply={apply}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
