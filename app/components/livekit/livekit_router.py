"""LiveKit token endpoints for admin live monitoring."""

from typing import Annotated

from fastapi import Body

from app.common.constants.messages import SuccessMessages
from app.common.response_models.livekit_responses import LiveKitTokenResponse
from app.common.responses import ApiResponse, success_response
from app.common.router import DefaultResponseRouter
from app.components.auth.auth_dependencies import AdminUser
from app.components.livekit import livekit_service
from app.core.dependencies import DB

livekit_router = DefaultResponseRouter()


@livekit_router.post(
    "/live-interviews/livekit-token", response_model=ApiResponse[LiveKitTokenResponse]
)
async def get_admin_livekit_token(
    db: DB,
    current_user: AdminUser,
    workspace_id: Annotated[str, Body(embed=True)],
) -> dict:
    """Generate a LiveKit token for an admin to watch a workspace's candidates."""
    result = livekit_service.generate_admin_token(current_user["_id"], workspace_id)
    return success_response(SuccessMessages.LIVEKIT_TOKEN_GENERATED, result)
