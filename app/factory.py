from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.lifespan import lifespan
from app.common.exception_handlers import register_exception_handlers
from app.components.auth.auth_router import router as auth_router
from app.components.workspace.workspace_router import router as workspace_router
from app.components.question_bank.question_router import router as question_router
from app.components.assessment.assessment_router import router as assessment_router
from app.components.candidate.candidate_router import router as candidate_router
from app.components.users.user_router import router as user_router


def create_application() -> FastAPI:
    app = FastAPI(
        title="SoftSuave Hire API",
        description="Enterprise Interview Platform API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

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
    app.include_router(candidate_router, prefix="/api/candidate", tags=["Candidate"])

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "service": "SoftSuave Hire API"}

    return app
