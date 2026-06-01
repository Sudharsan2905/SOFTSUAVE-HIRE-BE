from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.common.exception_handlers import register_exception_handlers
from app.common.middleware.logging_middleware import RequestLoggingMiddleware
from app.common.responses import ApiResponse, error_response, success_response
from app.components.assessment.assessment_router import public_router as assessment_public_router
from app.components.assessment.assessment_router import router as assessment_router
from app.components.auth.auth_router import router as auth_router
from app.components.candidate.candidate_router import router as candidate_router
from app.components.notifications.notification_router import router as notification_router
from app.components.question_bank.question_router import router as question_router
from app.components.users.user_router import router as user_router
from app.components.workspace.workspace_router import router as workspace_router
from app.core.config import settings
from app.core.lifespan import lifespan
from app.core.limiter import limiter


def _rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=429, content=error_response("Too many requests", str(exc)))


def create_application() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description=settings.APP_DESCRIPTION,
        version=settings.APP_VERSION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
    app.include_router(user_router, prefix="/api/users", tags=["Users"])
    app.include_router(workspace_router, prefix="/api/workspaces", tags=["Workspaces"])
    app.include_router(question_router, prefix="/api/questions", tags=["Question Bank"])
    app.include_router(assessment_router, prefix="/api", tags=["Assessments"])
    app.include_router(assessment_public_router, prefix="/api", tags=["Assessments"])
    app.include_router(candidate_router, prefix="/api/candidate", tags=["Candidate"])
    app.include_router(notification_router, prefix="/api/notifications", tags=["Notifications"])

    @app.get("/api/health", response_model=ApiResponse, tags=["Health"])
    async def health(request: Request) -> dict:
        try:
            db = request.app.state.db
            await db.command("ping")
            db_status = "ok"
        except Exception:
            db_status = "error"
        return success_response("Health check", {"status": "ok", "database": db_status})

    return app
