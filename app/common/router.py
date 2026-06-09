from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from app.common.responses import ApiResponse


class DefaultResponseRouter(APIRouter):
    """APIRouter that defaults every endpoint's response_model to ApiResponse.

    Override response_model per-endpoint only when a typed ApiResponse[T] is needed.
    Pass response_model=None explicitly to suppress the schema (e.g. file downloads).
    """

    def get(self, path: str, *, response_model: Any = ApiResponse, **kwargs: Any) -> Callable:
        return super().get(path, response_model=response_model, **kwargs)

    def post(self, path: str, *, response_model: Any = ApiResponse, **kwargs: Any) -> Callable:
        return super().post(path, response_model=response_model, **kwargs)

    def put(self, path: str, *, response_model: Any = ApiResponse, **kwargs: Any) -> Callable:
        return super().put(path, response_model=response_model, **kwargs)

    def patch(self, path: str, *, response_model: Any = ApiResponse, **kwargs: Any) -> Callable:
        return super().patch(path, response_model=response_model, **kwargs)

    def delete(self, path: str, *, response_model: Any = ApiResponse, **kwargs: Any) -> Callable:
        return super().delete(path, response_model=response_model, **kwargs)
