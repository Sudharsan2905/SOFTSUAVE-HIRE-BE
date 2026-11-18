"""Local-disk storage for pipeline resumes and photos (not S3)."""

import asyncio
import re
from pathlib import Path

from app.common.exceptions import NotFoundException, ValidationException
from app.core.config import settings


def _root() -> Path:
    root = Path(settings.PIPELINE_STORAGE_PATH).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_name(filename: str) -> str:
    base = Path(filename or "file").name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:100] or "file"


def _resolve(key: str) -> Path:
    path = (_root() / key).resolve()
    if _root() not in path.parents:
        raise ValidationException("Invalid storage key")
    return path


async def save(key: str, data: bytes) -> str:
    """Write ``data`` under ``key`` (relative path) and return the key."""
    path = _resolve(key)

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    await asyncio.to_thread(_write)
    return key


async def read(key: str) -> bytes:
    path = _resolve(key)
    if not path.is_file():
        raise NotFoundException("File not found")
    return await asyncio.to_thread(path.read_bytes)
