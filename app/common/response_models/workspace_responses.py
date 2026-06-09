from datetime import datetime

from pydantic import BaseModel


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime
