from bson.errors import InvalidId
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.common.exceptions import AppException
from app.common.responses import error_response
from app.core.logging import logger


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(exc.message, exc.detail or exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content=error_response("Validation failed", detail),
        )

    @app.exception_handler(InvalidId)
    async def invalid_object_id_handler(request: Request, exc: InvalidId) -> JSONResponse:
        """Any malformed ObjectId that reaches the database layer is the caller's mistake."""
        return JSONResponse(
            status_code=422,
            content=error_response("Validation failed", "Invalid ID format"),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never echo exception text (driver errors, paths, stack hints) to the client.
        logger.opt(exception=exc).error("Unhandled error: {} {}", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_response("Internal server error", "Internal server error"),
        )
