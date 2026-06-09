from pydantic import BaseModel


class LiveKitTokenResponse(BaseModel):
    token: str
    room: str
    workspace_id: str | None = None
