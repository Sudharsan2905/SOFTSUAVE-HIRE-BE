from typing import Any


def success_response(message: str, data: Any = None) -> dict:
    return {"success": True, "message": message, "data": data}


def error_response(message: str, detail: str = "") -> dict:
    return {"success": False, "message": message, "data": None, "detail": detail}
