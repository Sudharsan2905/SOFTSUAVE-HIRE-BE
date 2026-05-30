from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    success: bool
    message: str
    data: Any = None
    detail: str | None = None


def success_response(message: str, data: Any = None) -> dict:
    return {"success": True, "message": message, "data": data}


def error_response(message: str, detail: str = "") -> dict:
    return {"success": False, "message": message, "data": None, "detail": detail}
