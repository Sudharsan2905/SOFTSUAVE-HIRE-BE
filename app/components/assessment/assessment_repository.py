"""
Assessment repository — the single point of MongoDB access for the assessments
and assessment_submissions collections.

The service layer calls this repository instead of touching the database
directly.  This boundary means tests can inject a mock repository and
service logic stays fully unit-testable without a running DB.

Usage:
    from app.components.assessment.assessment_repository import AssessmentRepository

    repo = AssessmentRepository(db)
    assessment = await repo.find_active_by_id(workspace_id, assessment_id)
"""

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.messages import ErrorMessages
from app.common.constants.types import MongoDocument, PaginatedDocs
from app.common.exceptions import NotFoundException
from app.common.repositories.base_repository import BaseRepository
from app.common.utils import encode_permanent_sharelink, safe_regex, serialize_doc, utcnow


class AssessmentRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        super().__init__(db, "assessments")
        self._db = db

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def find_active_by_id(self, workspace_id: str, assessment_id: str) -> MongoDocument:
        """Return a single active assessment that belongs to the workspace.

        Raises:
            NotFoundException: if the assessment is missing, deleted, or in a different workspace.
        """
        return await self.find_one_or_raise(
            {
                "_id": ObjectId(assessment_id),
                "workspace_id": ObjectId(workspace_id),
                "is_active": True,
            },
            not_found_msg=ErrorMessages.ASSESSMENT_NOT_FOUND,
        )

    async def find_by_sharelink(self, share_link: str) -> MongoDocument | None:
        return await self.find_one({"share_link": share_link, "is_active": True})

    async def list_paginated_with_counts(
        self,
        workspace_id: str,
        search: str | None,
        sort_field: str,
        sort_dir: int,
        skip: int,
        limit: int,
    ) -> PaginatedDocs:
        """Return (total, docs) with a ``submission_count`` field injected per document."""
        query: dict = {"workspace_id": ObjectId(workspace_id), "is_active": True}
        if search:
            query["name"] = {"$regex": safe_regex(search), "$options": "i"}

        total, docs = await self.find_paginated(
            query,
            sort_field,
            sort_dir,
            skip,
            limit,
            allowed_sort_fields=["name", "created_at", "updated_at"],
        )
        # Enrich each doc with submission count (N+1, acceptable for paginated lists)
        for doc in docs:
            doc["submission_count"] = await self._db.assessment_submissions.count_documents(
                {"assessment_id": ObjectId(doc["id"])}
            )
        return total, docs

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create(self, document: dict) -> MongoDocument:
        """Insert a new assessment and back-fill its permanent share link."""
        doc = await self.insert_one(document)
        share_link = encode_permanent_sharelink(doc["id"])
        await self._col.update_one(
            {"_id": ObjectId(doc["id"])},
            {"$set": {"share_link": share_link}},
        )
        doc["share_link"] = share_link
        return doc

    async def update(
        self, workspace_id: str, assessment_id: str, update_fields: dict
    ) -> MongoDocument:
        """Validate ownership then apply a partial update.

        Raises:
            NotFoundException: if the assessment is not found in this workspace.
        """
        await self.find_active_by_id(workspace_id, assessment_id)
        return await self.update_by_id(assessment_id, update_fields)

    async def delete(self, workspace_id: str, assessment_id: str) -> None:
        """Soft-delete an assessment after verifying workspace ownership."""
        result = await self._col.update_one(
            {
                "_id": ObjectId(assessment_id),
                "workspace_id": ObjectId(workspace_id),
                "is_active": True,
            },
            {"$set": {"is_active": False, "updated_at": utcnow()}},
        )
        if result.matched_count == 0:
            raise NotFoundException(ErrorMessages.ASSESSMENT_NOT_FOUND)

    # ------------------------------------------------------------------
    # Submission queries (cross-collection, owned by this repository
    # because they are always scoped to an assessment)
    # ------------------------------------------------------------------

    async def get_submission(self, submission_id: str) -> MongoDocument:
        sub = await self._db.assessment_submissions.find_one({"_id": ObjectId(submission_id)})
        if not sub:
            raise NotFoundException(ErrorMessages.SUBMISSION_NOT_FOUND)
        return serialize_doc(sub)

    async def count_submissions(self, assessment_id: str) -> int:
        return await self._db.assessment_submissions.count_documents(
            {"assessment_id": ObjectId(assessment_id)}
        )
