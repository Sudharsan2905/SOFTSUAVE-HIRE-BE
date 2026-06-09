"""
Base async MongoDB repository.

All feature repositories should inherit from BaseRepository and receive
the collection name at construction.  This keeps every DB call in one
place and makes it trivial to swap collections in tests.

Example:
    class AssessmentRepository(BaseRepository):
        def __init__(self, db: AsyncIOMotorDatabase) -> None:
            super().__init__(db, "assessments")

        async def find_active(self, workspace_id: str) -> list[dict]:
            return await self.find_many({"workspace_id": ObjectId(workspace_id), "is_active": True})
"""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from app.common.constants.types import MongoDocument, PaginatedDocs
from app.common.exceptions import NotFoundException
from app.common.utils import safe_regex, serialize_doc, serialize_docs, utcnow


class BaseRepository:
    """Thin async wrapper around a single Motor collection.

    Every method serialises MongoDB documents via ``serialize_doc`` /
    ``serialize_docs`` so callers always receive plain dicts with string
    IDs — never raw BSON ObjectIds or datetimes.
    """

    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str) -> None:
        self._col: AsyncIOMotorCollection = db[collection_name]

    # ------------------------------------------------------------------
    # Single-document helpers
    # ------------------------------------------------------------------

    async def find_by_id(self, doc_id: str) -> MongoDocument | None:
        """Return a serialised document or ``None`` if not found."""
        doc = await self._col.find_one({"_id": ObjectId(doc_id)})
        return serialize_doc(doc) if doc else None

    async def find_by_id_or_raise(
        self, doc_id: str, not_found_msg: str = "Resource not found"
    ) -> MongoDocument:
        """Return a serialised document, raising ``NotFoundException`` when absent."""
        doc = await self.find_by_id(doc_id)
        if doc is None:
            raise NotFoundException(not_found_msg)
        return doc

    async def find_one(self, query: dict, projection: dict | None = None) -> MongoDocument | None:
        doc = await self._col.find_one(query, projection)
        return serialize_doc(doc) if doc else None

    async def find_one_or_raise(
        self, query: dict, not_found_msg: str = "Resource not found"
    ) -> MongoDocument:
        doc = await self.find_one(query)
        if doc is None:
            raise NotFoundException(not_found_msg)
        return doc

    # ------------------------------------------------------------------
    # Multi-document helpers
    # ------------------------------------------------------------------

    async def find_many(
        self,
        query: dict,
        sort: list[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int = 0,
        projection: dict | None = None,
    ) -> list[MongoDocument]:
        cursor = self._col.find(query, projection)
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        docs = await cursor.to_list(limit or None)
        return serialize_docs(docs)

    async def count(self, query: dict) -> int:
        return await self._col.count_documents(query)

    # ------------------------------------------------------------------
    # Paginated query
    # ------------------------------------------------------------------

    async def find_paginated(
        self,
        query: dict,
        sort_field: str,
        sort_dir: int,
        skip: int,
        limit: int,
        allowed_sort_fields: list[str],
        default_sort: str = "created_at",
    ) -> PaginatedDocs:
        """Run a paginated, sorted find query.

        Returns:
            (total_count, list_of_serialised_dicts)
        """
        safe_sort = sort_field if sort_field in allowed_sort_fields else default_sort
        total = await self._col.count_documents(query)
        docs = await (
            self._col.find(query).sort(safe_sort, sort_dir).skip(skip).limit(limit).to_list(limit)
        )
        return total, serialize_docs(docs)

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    async def insert_one(self, document: dict) -> MongoDocument:
        """Insert a document and return the serialised result (with id set)."""
        now = utcnow()
        document.setdefault("created_at", now)
        document.setdefault("updated_at", now)
        result = await self._col.insert_one(document)
        document["_id"] = result.inserted_id
        return serialize_doc(document)

    async def update_by_id(self, doc_id: str, update_fields: dict) -> MongoDocument:
        """Apply a ``$set`` update and return the refreshed document."""
        update_fields["updated_at"] = utcnow()
        await self._col.update_one({"_id": ObjectId(doc_id)}, {"$set": update_fields})
        return await self.find_by_id_or_raise(doc_id)

    async def soft_delete(self, doc_id: str, not_found_msg: str = "Resource not found") -> None:
        """Set ``is_active=False`` on a document, treating 0 matches as not-found."""
        result = await self._col.update_one(
            {"_id": ObjectId(doc_id), "is_active": True},
            {"$set": {"is_active": False, "updated_at": utcnow()}},
        )
        if result.matched_count == 0:
            raise NotFoundException(not_found_msg)

    async def hard_delete(self, doc_id: str, not_found_msg: str = "Resource not found") -> None:
        result = await self._col.delete_one({"_id": ObjectId(doc_id)})
        if result.deleted_count == 0:
            raise NotFoundException(not_found_msg)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def build_search_filter(self, field: str, term: str) -> dict:
        """Build a case-insensitive MongoDB regex filter for a single field."""
        return {field: {"$regex": safe_regex(term), "$options": "i"}}

    def build_multi_field_search(self, fields: list[str], term: str) -> dict:
        """Build ``$or`` regex filter across multiple fields."""
        escaped = safe_regex(term)
        return {"$or": [{f: {"$regex": escaped, "$options": "i"}} for f in fields]}

    async def exists(self, query: dict) -> bool:
        return await self._col.count_documents(query, limit=1) > 0
