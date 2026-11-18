"""Analysis agent: compares one candidate's profile with the JD (informational only)."""

from pydantic import BaseModel, Field

from app.components.ai_pipeline.llm_client import chat_json
from app.core.config import settings

SYSTEM = (
    "You are a technical recruiter. Compare the candidate profile with the job description. "
    "Respond with a single JSON object with exactly these keys: "
    "name (string|null), email (string|null), match_score (integer 0-100), "
    "matched_skills (string[]), missing_skills (string[]), experience_years (number|null), "
    "strengths (string[]), gaps (string[]), "
    "focus_topics (string[] - up to 6 technical topics worth probing in an interview), "
    "suitable (boolean), summary (string, max 2 sentences). "
    "Use only facts present in the profile. Do not invent details."
)


class CandidateAnalysis(BaseModel):
    name: str | None = None
    email: str | None = None
    match_score: int = Field(0, ge=0, le=100)
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    experience_years: float | None = None
    strengths: list[str] = []
    gaps: list[str] = []
    focus_topics: list[str] = []
    suitable: bool = False
    summary: str = ""


async def analyze_candidate(
    jd_text: str, profile_text: str, description: str
) -> CandidateAnalysis:
    """Score one candidate profile against the JD."""
    user = (
        f"JOB DESCRIPTION:\n{jd_text[:6000]}\n\n"
        f"CANDIDATE PROFILE:\n{profile_text}\n\n"
        f"ADDITIONAL DETAILS FROM HR:\n{description[:2000] or 'None'}"
    )
    data = await chat_json(settings.OPENAI_ANALYSIS_MODEL, SYSTEM, user, max_tokens=900)
    return CandidateAnalysis.model_validate(data)
