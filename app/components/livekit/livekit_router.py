"""LiveKit token endpoints for admin live monitoring."""

from typing import Annotated

from fastapi import APIRouter, Body

from app.common.responses import ApiResponse, success_response
from app.components.auth.auth_dependencies import AdminUser
from app.components.livekit import livekit_service
from app.core.dependencies import DB

livekit_router = APIRouter()


@livekit_router.post("/live-interviews/livekit-token", response_model=ApiResponse)
async def get_admin_livekit_token(
    db: DB,
    current_user: AdminUser,
    workspace_id: Annotated[str, Body(embed=True)],
) -> dict:
    """Generate a LiveKit token for an admin to watch a workspace's candidates."""
    result = livekit_service.generate_admin_token(current_user["_id"], workspace_id)
    return success_response("LiveKit token generated", result)
