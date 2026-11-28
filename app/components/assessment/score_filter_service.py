"""Score Filter: a flat list of candidates' submissions across several assessments.

Admins pick assessments of one workspace and (optionally) type a pass percentage. Every
submission in a selected assessment becomes one **entry** (candidate x assessment). Entries are not
grouped: a candidate who took two of the selected assessments appears twice, and nothing is
de-duplicated. Pass/Fail is derived from the typed percentage against the entry's own percentage,
inclusively, exactly like ``result.evaluate_result``.

Nothing here is stored. The typed percentage is a temporary filter value; the assessments'
own ``pass_percentage`` is untouched, so changing the number never needs a bulk update.

An entry is only judged when its submission is ``completed`` and fully scored
(``result.is_final``). Anything else is ``incomplete``, with the submission's state in ``status``.
Admin-facing only: never add this to candidate payloads.
"""

import re
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.constants.app_constants import SubmissionResult, SubmissionStatus
from app.common.exceptions import ValidationException
from app.common.utils import build_pagination_meta, paginate_query, safe_regex
from app.components.assessment.result import is_final

SCORING_PENDING = "scoring_pending"
INCOMPLETE = "incomplete"
_MAX_SUBMISSIONS = 50_000  # safety cap: at most 10 assessments x their candidates

_ERR_PERCENTAGE_REQUIRED = "pass_percentage is required to filter by passed or failed"


async def _fetch_submissions(db: AsyncIOMotorDatabase, assessment_oids: list[ObjectId]) -> list:
    """Lean projection of the selected assessments' submissions (no question text or answers).

    Each round is reduced to its score and question count, which is all an entry needs.
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


def _state(sub: dict) -> str:
    """The submission's state shown on its entry (see ``ScoreFilterEntry``)."""
    if is_final(sub):
        return str(SubmissionStatus.COMPLETED.value)
    if sub.get("status") == SubmissionStatus.COMPLETED:
        return SCORING_PENDING
    return str(sub.get("status") or SubmissionStatus.PENDING.value)


def _score_and_total(sub: dict) -> tuple[int, int]:
    rounds = sub.get("rounds_data") or []
    return (
        sum(int(rd.get("score") or 0) for rd in rounds),
        sum(int(rd.get("total") or 0) for rd in rounds),
    )


def _judge(percentage: float, pass_percentage: float | None) -> str | None:
    """Inclusive pass mark; ``None`` while no percentage has been typed."""
    if pass_percentage is None:
        return None
    passed = percentage >= pass_percentage
    return str((SubmissionResult.PASSED if passed else SubmissionResult.FAILED).value)


def _build_entry(sub: dict, names: dict[ObjectId, str], pass_percentage: float | None) -> dict:
    """One flat entry for one submission. ``candidate_id`` is swapped for ``candidate`` later."""
    entry: dict[str, Any] = {
        "candidate_id": sub["candidate_id"],
        "assessment": {
            "id": str(sub["assessment_id"]),
            "name": names.get(sub["assessment_id"], ""),
        },
        "status": _state(sub),
        "score": None,
        "total": None,
        "percentage": None,
        "result": INCOMPLETE,
    }
    if is_final(sub):
        score, total = _score_and_total(sub)
        # Rounded to one decimal like the stored percentages, so what the admin sees decides.
        percentage = round(float(sub.get("percentage") or 0.0), 1)
        entry.update(
            score=score,
            total=total,
            percentage=percentage,
            result=_judge(percentage, pass_percentage),
        )
    return entry


async def _attach_candidates(db: AsyncIOMotorDatabase, entries: list[dict]) -> None:
    ids = list({e["candidate_id"] for e in entries})
    users = {
        u["_id"]: u
        for u in await db.users.find(
            {"_id": {"$in": ids}}, {"first_name": 1, "last_name": 1, "email": 1}
        ).to_list(len(ids) or 1)
    }
    for entry in entries:
        user = users.get(entry.pop("candidate_id"), {})
        email = user.get("email")
        name = f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
        entry["candidate"] = {
            "id": str(user.get("_id", "")),
            "name": name or email or "",
            "email": email,
        }


def _matches_search(entry: dict, pattern: re.Pattern[str]) -> bool:
    cand = entry["candidate"]
    return bool(pattern.search(cand["name"]) or pattern.search(cand["email"] or ""))


def _summarize(entries: list[dict]) -> dict:
    counts = {"total": len(entries), "completed": 0, "passed": 0, "failed": 0, "incomplete": 0}
    for entry in entries:
        if entry["result"] == INCOMPLETE:
            counts["incomplete"] += 1
            continue
        counts["completed"] += 1
        if entry["result"] in (SubmissionResult.PASSED, SubmissionResult.FAILED):
            counts[str(entry["result"])] += 1
    return counts


def _sort_entries(
    entries: list[dict], sort_by: str, sort_order: str, position: dict[str, int]
) -> list[dict]:
    """Name first (stable tie-break), then percentage; unscored entries always go last.

    ``position`` is each assessment's place in the request, so one candidate's entries stay
    together and in the order the assessments were picked whenever the sort key ties.
    """
    reverse = sort_order == "desc"
    base = sorted(
        entries,
        key=lambda e: (
            e["candidate"]["name"].lower(),
            e["candidate"]["id"],
            position[e["assessment"]["id"]],
        ),
    )
    if sort_by == "name":
        return sorted(base, key=lambda e: e["candidate"]["name"].lower(), reverse=reverse)
    scored = [e for e in base if e["percentage"] is not None]
    unscored = [e for e in base if e["percentage"] is None]
    scored.sort(key=lambda e: e["percentage"], reverse=reverse)
    return scored + unscored


async def _query_entries(
    db: AsyncIOMotorDatabase,
    assessment_oids: list[ObjectId],
    pass_percentage: float | None,
    result: str,
    search: str | None,
    sort_by: str,
    sort_order: str,
) -> tuple[dict[ObjectId, str], dict, list[dict]]:
    """Every matching entry, sorted: ``(assessment names, summary, entries)``.

    The summary is taken before the result filter, so the cards keep their totals.
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
    entries = [_build_entry(sub, names, pass_percentage) for sub in submissions]
    await _attach_candidates(db, entries)
    if search and search.strip():
        pattern = re.compile(safe_regex(search.strip()), re.IGNORECASE)
        entries = [e for e in entries if _matches_search(e, pattern)]

    summary = _summarize(entries)
    if result != "all":
        entries = [e for e in entries if e["result"] == result]
    position = {str(oid): i for i, oid in enumerate(assessment_oids)}
    return names, summary, _sort_entries(entries, sort_by, sort_order, position)


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
    """One page of flat entries across ``assessment_oids`` (already tenant-verified).

    Raises:
        ValidationException: If a passed/failed filter is asked for without a pass percentage.
    """
    names, summary, entries = await _query_entries(
        db, assessment_oids, pass_percentage, result, search, sort_by, sort_order
    )
    skip, limit = paginate_query(page, page_size)
    return {
        "assessments": [{"id": str(o), "name": names.get(o, "")} for o in assessment_oids],
        "pass_percentage": pass_percentage,
        "summary": summary,
        "entries": entries[skip : skip + limit],
        "pagination": build_pagination_meta(len(entries), page, page_size),
    }


async def export_score_filter(
    db: AsyncIOMotorDatabase,
    assessment_oids: list[ObjectId],
    pass_percentage: float | None,
    result: str,
    search: str | None,
    sort_by: str,
    sort_order: str,
) -> list[dict]:
    """Every matching entry (no paging) in a flat format suitable for Excel export.

    Takes the same filters as ``get_score_filter``, so "export with filters" matches the table.

    Raises:
        ValidationException: If a passed/failed filter is asked for without a pass percentage.
    """
    _, _, entries = await _query_entries(
        db, assessment_oids, pass_percentage, result, search, sort_by, sort_order
    )
    return [
        {
            "name": e["candidate"]["name"],
            "email": e["candidate"]["email"],
            "assessment": e["assessment"]["name"],
            "status": e["status"],
            "score": e["score"],
            "total": e["total"],
            "percentage": e["percentage"],
            "result": e["result"],
        }
        for e in entries
    ]
