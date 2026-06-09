from typing import Annotated, Any, TypeVar

from pydantic import Field

# ---------------------------------------------------------------------------
# Generic type variables
# ---------------------------------------------------------------------------
T = TypeVar("T")
ModelT = TypeVar("ModelT")

# ---------------------------------------------------------------------------
# Raw MongoDB document
# ---------------------------------------------------------------------------
MongoDocument = dict[str, Any]

# ---------------------------------------------------------------------------
# ID type — string representation of a MongoDB ObjectId
# ---------------------------------------------------------------------------
DocumentId = str

# ---------------------------------------------------------------------------
# Annotated pagination query param types
# Attach these directly to FastAPI Query parameters for automatic validation.
#
# Example:
#   async def list_items(page: PageNumber = 1, page_size: PageSize = 20) -> ...:
# ---------------------------------------------------------------------------
PageNumber = Annotated[int, Field(ge=1, description="Page number (1-based)")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Items per page (max 100)")]

# ---------------------------------------------------------------------------
# Sort helpers
# ---------------------------------------------------------------------------
SortField = str
SortDirection = int  # pymongo: 1 = ascending, -1 = descending

# ---------------------------------------------------------------------------
# Service return shorthand — typed alias for the common (total, docs) tuple
# returned by paginated repository queries.
# ---------------------------------------------------------------------------
PaginatedDocs = tuple[int, list[MongoDocument]]
