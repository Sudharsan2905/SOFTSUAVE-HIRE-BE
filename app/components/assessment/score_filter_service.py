"""Score Filter: combine several assessments and judge candidates against one pass mark.

Admins pick assessments of one workspace and (optionally) type a pass percentage. For every
candidate who has a submission in any selected assessment we compute a **combined score**:
total correct answers over total questions across all selected assessments (so assessments with
more questions weigh more). Pass/Fail is then derived from the typed percentage, inclusively,
exactly like ``result.evaluate_result``.

Nothing here is stored. The typed percentage is a temporary filter value; the assessments'
own ``pass_percentage`` is untouched, so changing the number never needs a bulk update.

A candidate is only judged when **every** selected assessment is ``completed`` and fully scored
(``result.is_final``). Anyone else is ``incomplete``, with the state of each assessment in the
breakdown. Admin-facing only: never add this to candidate payloads.
"""

import re
from collections import defaultdict
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import SubmissionResult, SubmissionStatus
from app.common.exceptions import ValidationException
from app.common.utils import build_pagination_meta, paginate_query, safe_regex
from app.components.assessment.result import is_final

NOT_ATTEMPTED = "not_attempted"
SCORING_PENDING = "scoring_pending"
INCOMPLETE = "incomplete"
_MAX_SUBMISSIONS = 50_000  # safety cap: at most 10 assessments x their candidates

_ERR_PERCENTAGE_REQUIRED = "pass_percentage is required to filter by passed or failed"


async def _fetch_submissions(db: AsyncIOMotorDatabase, assessment_oids: list[ObjectId]) -> list:
    """Lean projection of the selected assessments' submissions (no question text or answers).

    Each round is reduced to its score and question count, which is all the combined score needs.
    """
    pipeline: list[dict[str, Any]] = [
        {"$match": {"assessment_id": {"$in": assessment_oids}, "is_active": {"$ne": False}}},
        {
            "$project": {
                "candidate_id": 1,
                "assessment_id": 1,
                "status": 1,
                "percentage": 1,
                "scoring_status": 1,
                "scoring_completed_at": 1,
                "rounds_data": {
                    "$map": {
                        "input": {"$ifNull": ["$rounds_data", []]},
                        "as": "rd",
                        "in": {
                            "score": "$$rd.score",
                            "total": {"$size": {"$ifNull": ["$$rd.questions", []]}},
                        },
                    }
                },
            }
        },
    ]
    return await db.assessment_submissions.aggregate(pipeline).to_list(_MAX_SUBMISSIONS)


def _state(sub: dict | None) -> str:
    """The submission's state for the breakdown (see ``ScoreFilterBreakdownItem``)."""
    if sub is None:
        return NOT_ATTEMPTED
    if is_final(sub):
        return str(SubmissionStatus.COMPLETED.value)
    if sub.get("status") == SubmissionStatus.COMPLETED:
        return SCORING_PENDING
    return str(sub.get("status") or NOT_ATTEMPTED)


def _score_and_total(sub: dict) -> tuple[int, int]:
    rounds = sub.get("rounds_data") or []
    return (
        sum(int(rd.get("score") or 0) for rd in rounds),
        sum(int(rd.get("total") or 0) for rd in rounds),
    )


def _judge(combined_percentage: float, pass_percentage: float | None) -> str | None:
    """Inclusive pass mark; ``None`` while no percentage has been typed."""
    if pass_percentage is None:
        return None
    passed = combined_percentage >= pass_percentage
    return str((SubmissionResult.PASSED if passed else SubmissionResult.FAILED).value)


def _build_row(
    candidate_id: ObjectId,
    subs: dict[ObjectId, dict],
    assessment_oids: list[ObjectId],
    pass_percentage: float | None,
) -> dict:
    breakdown: list[dict] = []
    score_sum = total_sum = done = 0
    for oid in assessment_oids:
        sub = subs.get(oid)
        item: dict[str, Any] = {
            "assessment_id": str(oid),
            "status": _state(sub),
            "score": None,
            "total": None,
            "percentage": None,
        }
        if sub is not None and is_final(sub):
            score, total = _score_and_total(sub)
            item.update(
                score=score, total=total, percentage=round(float(sub.get("percentage") or 0.0), 1)
            )
            score_sum += score
            total_sum += total
            done += 1
        breakdown.append(item)

    row: dict[str, Any] = {
        "candidate_id": candidate_id,
        "completed_count": done,
        "combined_score": None,
        "combined_total": None,
        "combined_percentage": None,
        "result": INCOMPLETE,
        "breakdown": breakdown,
    }
    if done == len(assessment_oids):
        # Rounded to one decimal like the stored percentages, so what the admin sees decides.
        percentage = round(score_sum / total_sum * 100, 1) if total_sum else 0.0
        row.update(
            combined_score=score_sum,
            combined_total=total_sum,
            combined_percentage=percentage,
            result=_judge(percentage, pass_percentage),
        )
    return row


async def _attach_candidates(db: AsyncIOMotorDatabase, rows: list[dict]) -> None:
    ids = [r["candidate_id"] for r in rows]
    users = {
        u["_id"]: u
        for u in await db.users.find(
            {"_id": {"$in": ids}}, {"first_name": 1, "last_name": 1, "email": 1}
        ).to_list(len(ids) or 1)
    }
    for row in rows:
        user = users.get(row.pop("candidate_id"), {})
        email = user.get("email")
        name = f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
        row["candidate"] = {
            "id": str(user.get("_id", "")),
            "name": name or email or "",
            "email": email,
        }


def _matches_search(row: dict, pattern: re.Pattern[str]) -> bool:
    cand = row["candidate"]
    return bool(pattern.search(cand["name"]) or pattern.search(cand["email"] or ""))


def _summarize(rows: list[dict]) -> dict:
    counts = {"total": len(rows), "completed": 0, "passed": 0, "failed": 0, "incomplete": 0}
    for row in rows:
        if row["result"] == INCOMPLETE:
            counts["incomplete"] += 1
            continue
        counts["completed"] += 1
        if row["result"] in (SubmissionResult.PASSED, SubmissionResult.FAILED):
            counts[str(row["result"])] += 1
    return counts


def _sort_rows(rows: list[dict], sort_by: str, sort_order: str) -> list[dict]:
    """Name first (stable tie-break), then combined %; candidates without one always go last."""
    reverse = sort_order == "desc"
    by_name = sorted(rows, key=lambda r: r["candidate"]["name"].lower())
    if sort_by == "name":
        return sorted(by_name, key=lambda r: r["candidate"]["name"].lower(), reverse=reverse)
    scored = [r for r in by_name if r["combined_percentage"] is not None]
    unscored = [r for r in by_name if r["combined_percentage"] is None]
    scored.sort(key=lambda r: r["combined_percentage"], reverse=reverse)
    return scored + unscored


async def get_score_filter(
    db: AsyncIOMotorDatabase,
    assessment_oids: list[ObjectId],
    pass_percentage: float | None,
    result: str,
    search: str | None,
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
) -> dict:
    """Combined results for candidates across ``assessment_oids`` (already tenant-verified).

    Raises:
        ValidationException: If a passed/failed filter is asked for without a pass percentage.
    """
    if result in ("passed", "failed") and pass_percentage is None:
        raise ValidationException(_ERR_PERCENTAGE_REQUIRED)

    names = {
        a["_id"]: a.get("name", "")
        for a in await db.assessments.find({"_id": {"$in": assessment_oids}}, {"name": 1}).to_list(
            len(assessment_oids)
        )
    }
    submissions = await _fetch_submissions(db, assessment_oids)
    by_candidate: dict[ObjectId, dict[ObjectId, dict]] = defaultdict(dict)
    for sub in submissions:
        by_candidate[sub["candidate_id"]][sub["assessment_id"]] = sub

    rows = [
        _build_row(cid, subs, assessment_oids, pass_percentage)
        for cid, subs in by_candidate.items()
    ]
    await _attach_candidates(db, rows)
    if search and search.strip():
        pattern = re.compile(safe_regex(search.strip()), re.IGNORECASE)
        rows = [r for r in rows if _matches_search(r, pattern)]

    summary = _summarize(rows)
    if result != "all":
        rows = [r for r in rows if r["result"] == result]
    rows = _sort_rows(rows, sort_by, sort_order)

    skip, limit = paginate_query(page, page_size)
    return {
        "assessments": [{"id": str(o), "name": names.get(o, "")} for o in assessment_oids],
        "pass_percentage": pass_percentage,
        "summary": summary,
        "candidates": rows[skip : skip + limit],
        "pagination": build_pagination_meta(len(rows), page, page_size),
    }
