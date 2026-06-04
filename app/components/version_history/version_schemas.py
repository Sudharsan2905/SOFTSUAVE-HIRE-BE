from pydantic import BaseModel


class VersionHistoryListResponse(BaseModel):
    version: int
    status: str
    score: int
    percentage: float
    malpractice_count: int
    reaccess_reason: str | None = None
    reaccess_reason_category: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
