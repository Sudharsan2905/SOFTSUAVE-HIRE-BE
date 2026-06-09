from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.common.schemas.pagination import PaginationMeta

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """
    Standard envelope for every API response.

    Usage in routers:
        response_model=ApiResponse[AssessmentDetailResponse]

    Usage in services (return typed instance):
        return ApiResponse(success=True, message=SuccessMessages.ASSESSMENT_CREATED, data=payload)
    """

    success: bool
    message: str
    data: T | None = None
    detail: str | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Typed paginated envelope.

    Usage in routers:
        response_model=PaginatedResponse[AssessmentListItemResponse]
    """

    success: bool
    message: str
    data: list[T]
    pagination: PaginationMeta


# ---------------------------------------------------------------------------
# Convenience factory helpers — return plain dicts so FastAPI can serialise
# them via the response_model.  Services that return Pydantic instances
# directly should use ApiResponse(...) / PaginatedResponse(...) instead.
# ---------------------------------------------------------------------------


def success_response(message: str, data: Any = None) -> dict:
    return {"success": True, "message": message, "data": data}


def paginated_success_response(message: str, data: list, pagination: dict) -> dict:
    return {"success": True, "message": message, "data": data, "pagination": pagination}


def error_response(message: str, detail: str = "") -> dict:
    return {"success": False, "message": message, "data": None, "detail": detail}
