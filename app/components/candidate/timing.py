"""Server-owned round timing.

The server — never the browser — decides when a round ends.  Each submission carries
three denormalised top-level fields describing the *current* round:

    current_deadline_at       datetime  – when the round must end
    current_round_seconds     int       – full length of the round
    round_timer_started       bool      – False until the candidate actually begins the round

While ``round_timer_started`` is False the deadline is provisional (setup allowance +
duration) so abandoned sessions can still be reaped; ``remaining_seconds`` reports the
full duration in that state.  Documents created before this module existed have none of
these fields; they are handled by deriving values from ``rounds_data``.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.common.constants.app_constants import SubmissionStatus
from app.common.utils import utcnow
from app.core.config import settings


def aware(dt: datetime | None) -> datetime | None:
    """Mongo returns naive UTC datetimes; normalise to tz-aware UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def round_seconds(rd: dict | None) -> int:
    """Length of a round in seconds. The only place a default duration exists."""
    minutes = (rd or {}).get("max_duration_minutes") or settings.DEFAULT_ROUND_MINUTES
    return int(minutes) * 60


def current_round_doc(sub: dict) -> dict | None:
    current = sub.get("current_round", 1)
    return next(
        (rd for rd in sub.get("rounds_data") or [] if rd.get("round_number") == current), None
    )


def provisional_round_fields(now: datetime, rd: dict) -> dict[str, Any]:
    """Top-level fields for a round that has been assigned but not yet started."""
    secs = round_seconds(rd)
    return {
        "current_round_seconds": secs,
        "round_timer_started": False,
        "current_deadline_at": now
        + timedelta(seconds=secs + settings.SETUP_ALLOWANCE_MINUTES * 60),
    }


def started_round_fields(now: datetime, seconds: int) -> dict[str, Any]:
    return {
        "current_round_seconds": seconds,
        "round_timer_started": True,
        "current_deadline_at": now + timedelta(seconds=seconds),
    }


def timer_started(sub: dict) -> bool:
    flag = sub.get("round_timer_started")
    if flag is not None:
        return bool(flag)
    # Legacy document: the round counts as started once it has a started_at.
    rd = current_round_doc(sub)
    return bool(rd and rd.get("started_at"))


def effective_deadline(sub: dict) -> datetime | None:
    deadline = aware(sub.get("current_deadline_at"))
    if deadline is not None:
        return deadline
    rd = current_round_doc(sub)
    started = aware(rd.get("started_at")) if rd else None
    if started is None:
        return None
    return started + timedelta(seconds=round_seconds(rd))


def remaining_seconds(sub: dict, now: datetime | None = None) -> int:
    """Seconds left in the current round, computed from the server clock."""
    seconds_total = sub.get("current_round_seconds") or round_seconds(current_round_doc(sub))
    if not timer_started(sub):
        return int(seconds_total)
    deadline = effective_deadline(sub)
    if deadline is None:
        return int(seconds_total)
    reference = aware(sub.get("paused_at")) if _is_paused(sub) else None
    reference = reference or now or utcnow()
    return max(0, int((deadline - reference).total_seconds()))


def _is_paused(sub: dict) -> bool:
    return sub.get("status") == SubmissionStatus.ON_HOLD and sub.get("paused_at") is not None


def is_expired(sub: dict, grace_seconds: int = 0, now: datetime | None = None) -> bool:
    """True once the round deadline plus grace has passed (paused sessions never expire)."""
    if _is_paused(sub) or not timer_started(sub):
        return False
    deadline = effective_deadline(sub)
    if deadline is None:
        return False
    return (now or utcnow()) > deadline + timedelta(seconds=grace_seconds)
