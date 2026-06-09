from pydantic import BaseModel, ConfigDict

from app.common.constants.app_constants import CandidateType, UserRole


class UserInTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    first_name: str
    last_name: str
    email: str
    role: UserRole
    is_active: bool
    workspace_ids: list[str] = []
    default_workspace_id: str | None = None
    candidate_type: CandidateType | None = None
    phone: str | None = None


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    user: UserInTokenResponse


class TokenRefreshResponse(BaseModel):
    access_token: str
    token_type: str


class GoogleUserData(BaseModel):
    email: str
    first_name: str
    last_name: str
    google_id: str
    picture: str


class GooglePreAuthResponse(BaseModel):
    needs_registration: bool
    google_data: GoogleUserData
