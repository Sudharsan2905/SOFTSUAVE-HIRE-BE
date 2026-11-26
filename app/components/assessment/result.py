"""Pass/fail outcome of a submission, derived from the assessment's pass mark.

Nothing is stored on the submission: the result is computed from the submission's overall
``percentage`` and the assessment's ``pass_percentage`` every time it is read. Changing an
assessment's pass mark therefore re-evaluates every candidate automatically.

A result is only given to a submission that is ``completed`` *and* fully scored. Sessions that
ended as malpractice / terminated, are still running, or are waiting on (or failed) scoring have
no result (``None``), so those flows look exactly as they did before.

Results are admin-facing only; never add them to candidate-facing payloads.
"""

from typing import Any

from app.common.constants.app_constants import (
    DEFAULT_PASS_PERCENTAGE,
    SubmissionResult,
    SubmissionStatus,
)

_SCORED = "scored"


def effective_pass_percentage(assessment: dict | None) -> int:
    """The assessment's pass mark, or the default for assessments created before it existed."""
    value = (assessment or {}).get("pass_percentage")
    return int(value) if value else DEFAULT_PASS_PERCENTAGE


def _is_scored(submission: dict) -> bool:
    scoring_status = submission.get("scoring_status")
    if scoring_status is not None:
        return bool(scoring_status == _SCORED)
    # Submissions scored before ``scoring_status`` existed only carry a completion timestamp.
    return submission.get("scoring_completed_at") is not None


def evaluate_result(submission: dict, pass_percentage: int) -> str | None:
    """Return ``"passed"`` / ``"failed"``, or ``None`` when the submission has no result yet."""
    if submission.get("status") != SubmissionStatus.COMPLETED or not _is_scored(submission):
        return None
    percentage = float(submission.get("percentage") or 0.0)
    return SubmissionResult.PASSED if percentage >= pass_percentage else SubmissionResult.FAILED


def result_query(pass_percentage: int, passed: bool) -> dict[str, Any]:
    """Mongo filter matching completed + scored submissions that passed (or failed)."""
    return {
        "status": SubmissionStatus.COMPLETED,
        "percentage": {"$gte": pass_percentage} if passed else {"$lt": pass_percentage},
        "$or": [
            {"scoring_status": _SCORED},
            {"scoring_status": {"$exists": False}, "scoring_completed_at": {"$ne": None}},
        ],
    }
