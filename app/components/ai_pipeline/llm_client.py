"""Thin async wrapper around the OpenAI chat API that returns parsed JSON."""

import json
import re
from typing import Any

from app.common.exceptions import ExternalServiceException
from app.core.config import settings
from app.core.logging import logger


def parse_json(text: str) -> Any:
    """Parse model output, tolerating ```json fences and surrounding prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"[\[{].*[\]}]", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group())


def _fatal(exc: Exception, model: str) -> ExternalServiceException | None:
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return ExternalServiceException(
            "OpenAI rejected the API key (HTTP 401). Set a valid OPENAI_API_KEY and restart."
        )
    if isinstance(exc, openai.NotFoundError):
        return ExternalServiceException(
            f"OpenAI model '{model}' was not found or the key has no access to it. "
            "Change OPENAI_QUESTION_MODEL / OPENAI_ANALYSIS_MODEL."
        )
    if isinstance(exc, openai.RateLimitError) and "insufficient_quota" in str(exc):
        return ExternalServiceException(
            "OpenAI quota exhausted (insufficient_quota). Add billing credit to the key."
        )
    return None


async def chat_json(
    model: str, system: str, user: str, max_tokens: int = 2000, retries: int = 1
) -> Any:
    """Call the model in JSON mode and return the parsed object (502 on failure)."""
    if not settings.OPENAI_API_KEY:
        raise ExternalServiceException("OPENAI_API_KEY is not configured")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=120)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                max_completion_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return parse_json(response.choices[0].message.content or "")
        except Exception as exc:
            if fatal := _fatal(exc, model):
                raise fatal from exc
            logger.warning(f"LLM call attempt {attempt + 1} failed: {type(exc).__name__}: {exc}")
            last_exc = exc
    raise ExternalServiceException(f"LLM call failed ({type(last_exc).__name__}): {last_exc}")
