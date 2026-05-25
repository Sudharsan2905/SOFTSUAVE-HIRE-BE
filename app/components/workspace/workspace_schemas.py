from pydantic import BaseModel, Field
from typing import Optional, List


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class InviteMemberRequest(BaseModel):
    user_ids: List[str] = Field(..., min_length=1)
