"""Request schemas for the AI pipeline endpoints."""

from pydantic import BaseModel, Field


class CandidateInput(BaseModel):
    name: str | None = Field(None, max_length=120)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=30)
    description: str = Field("", max_length=5000)


class RetryRequest(BaseModel):
    step: str | None = None
