from typing import Annotated, Literal

from pydantic import BaseModel, Field

NotificationType = Literal["submission", "assessment", "interview", "system"]


class CreateNotificationRequest(BaseModel):
    type: NotificationType = "system"
    title: Annotated[str, Field(min_length=1, max_length=200)]
    message: Annotated[str, Field(min_length=1, max_length=1000)]
    link: str | None = None
