import base64
import hashlib
import hmac as _hmac
import json
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId


def utcnow() -> datetime:
    return datetime.now(UTC)


def generate_uuid() -> str:
    return str(uuid.uuid4())


def _link_secret() -> bytes:
    from app.core.config import settings

    key = (settings.JWT_SECRET_KEY or "softsuave-hire-default").encode()
    return key[:32].ljust(32, b"0")


def encode_permanent_sharelink(assessment_id: str) -> str:
    """Encode assessment_id into a tamper-proof share link."""
    payload = json.dumps({"a": assessment_id}, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = _hmac.new(_link_secret(), payload, hashlib.sha256).hexdigest()[:12]
    return f"{encoded}.{sig}"


def encode_expirable_sharelink(assessment_id: str, start_iso: str, end_iso: str) -> str:
    """Encode assessment_id + time window into a tamper-proof expirable link."""
    payload = json.dumps(
        {"a": assessment_id, "s": start_iso, "e": end_iso}, separators=(",", ":")
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = _hmac.new(_link_secret(), payload, hashlib.sha256).hexdigest()[:12]
    return f"{encoded}.{sig}"


def decode_sharelink(encoded_link: str) -> dict:
    """Decode and verify a share link. Returns payload dict or raises ValueError.
    Payload keys: "a" (assessment_id), optionally "s" (start_iso) and "e" (end_iso).
    """
    from app.common.exceptions import ValidationException

    try:
        dot_idx = encoded_link.rfind(".")
        if dot_idx < 0:
            raise ValueError("no signature")
        sig = encoded_link[dot_idx + 1 :]
        b64 = encoded_link[:dot_idx]
        padding = (4 - len(b64) % 4) % 4
        payload = base64.urlsafe_b64decode(b64 + "=" * padding)
        expected = _hmac.new(_link_secret(), payload, hashlib.sha256).hexdigest()[:12]
        if not _hmac.compare_digest(sig, expected):
            raise ValueError("signature mismatch")
        return json.loads(payload)  # type: ignore[no-any-return]
    except ValidationException:
        raise
    except Exception as err:
        raise ValidationException("Invalid or tampered share link") from err


def generate_sharelink(id: str) -> str:
    """Deprecated: use encode_permanent_sharelink instead."""
    current_time = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{id}-{current_time}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_secure_token() -> str:
    return secrets.token_urlsafe(64)


def serialize_doc(doc: dict | None) -> dict:
    if doc is None:
        return {}
    result: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            result["id"] = str(value)
        elif isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, list):
            result[key] = [
                (
                    serialize_doc(v)
                    if isinstance(v, dict)
                    else (str(v) if isinstance(v, ObjectId) else v)
                )
                for v in value
            ]
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        else:
            result[key] = value
    return result


def serialize_docs(docs: list) -> list:
    return [serialize_doc(doc) for doc in docs]


def paginate_query(page: int = 1, page_size: int = 20) -> tuple[int, int]:
    skip = (page - 1) * page_size
    return skip, page_size


def safe_regex(term: str) -> str:
    """Escape user input for safe use in MongoDB $regex queries (prevents ReDoS)."""
    return re.escape(term)


def build_pagination_meta(total: int, page: int, page_size: int) -> dict:
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "has_next": page * page_size < total,
        "has_prev": page > 1,
    }


async def list_paginated(
    collection: Any,
    query: dict,
    sort_field: str,
    sort_dir: int,
    skip: int,
    limit: int,
    allowed_sort_fields: list[str],
    default_sort: str = "created_at",
) -> tuple[int, list[dict]]:
    """Run a paginated, sorted MongoDB find query.

    Args:
        collection: Motor collection to query.
        query: MongoDB filter document.
        sort_field: Requested sort field (validated against allowed_sort_fields).
        sort_dir: 1 for ascending, -1 for descending.
        skip: Number of documents to skip.
        limit: Maximum documents to return.
        allowed_sort_fields: Whitelist of valid sort fields.
        default_sort: Fallback sort field when sort_field is invalid.

    Returns:
        Tuple of (total_count, list_of_documents).
    """
    safe_sort = sort_field if sort_field in allowed_sort_fields else default_sort
    total = await collection.count_documents(query)
    docs = (
        await collection.find(query)
        .sort(safe_sort, sort_dir)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return total, docs
