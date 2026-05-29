import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from bson import ObjectId


def utcnow() -> datetime:
    return datetime.now(UTC)


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_sharelink(id: str) -> str:
    current_time = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{id}-{current_time}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_secure_token() -> str:
    return secrets.token_urlsafe(64)


def serialize_doc(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    result = {}
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


def build_pagination_meta(total: int, page: int, page_size: int) -> dict:
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "has_next": page * page_size < total,
        "has_prev": page > 1,
    }
