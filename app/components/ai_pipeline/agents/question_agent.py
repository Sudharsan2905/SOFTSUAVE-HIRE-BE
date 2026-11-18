"""Question agent: ONE call per JD producing a shared question set for all candidates."""

import secrets
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.common.constants.app_constants import QuestionType
from app.common.exceptions import ExternalServiceException
from app.components.ai_pipeline.constants import MIN_ESSAY_ANSWER_CHARS, QUESTION_COUNTS
from app.components.ai_pipeline.llm_client import chat_json
from app.core.config import settings
from app.core.logging import logger

SYSTEM = (
    "You are a senior technical interviewer. Create interview questions as JSON: "
    '{"questions": [...]}. Each question has: question_text (string; wrap code in fenced '
    "markdown blocks), question_type (mcq_single|mcq_multi|essay), complexity (low|medium|high), "
    "options (array of {text, is_correct}; exactly 4 options for MCQ; mcq_single has exactly one "
    "correct, mcq_multi has 2 or 3 correct; omit for essay), correct_answer (string). "
    "MANDATORY: every essay question MUST include a complete, specific model answer in "
    "correct_answer (at least 3 sentences covering the key points an evaluator must look for); "
    "an essay without it is rejected. Cover the JD's core skills and the candidates' common focus "
    "topics. Mix complexity levels. No duplicates."
)


class _Option(BaseModel):
    id: str = Field(..., min_length=1)
    text: str
    is_correct: bool = False

    @model_validator(mode="after")
    def _text(self) -> "_Option":
        if not self.text.strip():
            raise ValueError("Each option must have text")
        return self


class QuestionSpec(BaseModel):
    """Validates one generated question."""

    question_text: str
    question_type: QuestionType
    complexity: str
    options: list[_Option] | None = None
    correct_answer: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "QuestionSpec":
        if len(self.question_text.strip()) < 5:
            raise ValueError("Question must have text (min 5 characters)")
        options = self.options or []
        if self.question_type == QuestionType.ESSAY:
            if options:
                raise ValueError("Essay questions must not have options")
            if len((self.correct_answer or "").strip()) < MIN_ESSAY_ANSWER_CHARS:
                raise ValueError("Essay questions need a model answer")
            return self
        correct = [o for o in options if o.is_correct]
        if len(options) < 2:
            raise ValueError("MCQ questions need at least 2 options")
        if not correct:
            raise ValueError("MCQ questions must have at least one correct option")
        if self.question_type == QuestionType.MCQ_SINGLE and len(correct) != 1:
            raise ValueError("Single-choice questions must have exactly one correct option")
        if self.question_type == QuestionType.MCQ_MULTI and len(correct) < 2:
            raise ValueError("Multi-choice questions must have at least two correct options")
        return self


def summarize_candidates(analyses: list[dict]) -> str:
    """Compact aggregate: ranked skills/topics plus a short per-candidate line."""
    topics: Counter[str] = Counter()
    gaps: Counter[str] = Counter()
    lines = []
    for a in analyses:
        topics.update(t.strip().lower() for t in a.get("focus_topics", []) if t.strip())
        gaps.update(t.strip().lower() for t in a.get("missing_skills", []) if t.strip())
        lines.append(f"- {a.get('name') or 'Candidate'}: {', '.join(a.get('matched_skills', [])[:8])}")
    return (
        f"Top focus topics: {', '.join(t for t, _ in topics.most_common(15))}\n"
        f"Common skill gaps: {', '.join(t for t, _ in gaps.most_common(8))}\n"
        f"Candidates ({len(analyses)}):\n" + "\n".join(lines[:60])
    )


def normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate one raw question; return a Mongo-ready dict (essay options == []) or None."""
    q = dict(raw)
    if q.get("complexity") not in ("low", "medium", "high"):
        q["complexity"] = "medium"
    if q.get("question_type") == "essay":
        q["options"] = []
        answer = q.get("correct_answer")
        q["correct_answer"] = answer.strip() if isinstance(answer, str) else None
    else:
        q["options"] = [
            {"id": chr(97 + i), "text": str(o.get("text", "")), "is_correct": bool(o.get("is_correct"))}
            for i, o in enumerate(q.get("options") or [])
            if isinstance(o, dict)
        ]
        q["correct_answer"] = None
    try:
        QuestionSpec.model_validate(q)
    except (ValidationError, ValueError):
        return None
    return q


async def _request(jd: str, summary: str, counts: dict[str, int]) -> list[dict]:
    spec = ", ".join(f"{n} x {t}" for t, n in counts.items() if n > 0)
    user = f"JOB DESCRIPTION:\n{jd[:6000]}\n\nCANDIDATE POOL:\n{summary}\n\nCreate exactly: {spec}."
    data = await chat_json(settings.OPENAI_QUESTION_MODEL, SYSTEM, user, max_tokens=8000, retries=1)
    items = data.get("questions") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def _accept(raws: list, result: dict[str, list[dict]], counts: dict[str, int]) -> None:
    seen = {q["question_text"].strip().lower() for qs in result.values() for q in qs}
    for raw in raws:
        q = normalize(raw) if isinstance(raw, dict) else None
        if not q:
            continue
        t = q["question_type"]
        key = q["question_text"].strip().lower()
        if t in result and len(result[t]) < counts[t] and key not in seen:
            result[t].append(q)
            seen.add(key)


async def generate_questions(
    jd_text: str, analyses: list[dict], counts: dict[str, int] | None = None
) -> list[dict]:
    """Return exactly sum(counts) validated questions, regenerating only the shortfall (3 passes)."""
    counts = counts or dict(QUESTION_COUNTS)
    summary = summarize_candidates(analyses)
    result: dict[str, list[dict]] = {t: [] for t in counts}
    need = dict(counts)
    for attempt in range(3):
        _accept(await _request(jd_text, summary, need), result, counts)
        need = {t: counts[t] - len(result[t]) for t in counts}
        if not any(need.values()):
            break
        logger.warning(f"Question agent shortfall (attempt {attempt + 1}): {need}")
    if any(need.values()):
        raise ExternalServiceException(f"Could not generate the required questions, missing: {need}")
    questions = [q for t in counts for q in result[t]]
    secrets.SystemRandom().shuffle(questions)
    return questions
