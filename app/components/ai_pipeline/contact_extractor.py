"""Resolve a candidate's email/name from resume text and the free-text description."""

import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_NAME_RE = re.compile(r"(?im)^\s*(?:full\s+)?name\s*[:\-]\s*(.+?)\s*$")


def find_email(text: str) -> str | None:
    """Return the first email address found in ``text``."""
    match = _EMAIL_RE.search(text or "")
    return match.group().lower().rstrip(".") if match else None


def resolve_email(profile_text: str, description: str) -> tuple[str | None, str | None]:
    """Return (email, source) where source is 'resume' or 'description'."""
    if email := find_email(profile_text):
        return email, "resume"
    if email := find_email(description):
        return email, "description"
    return None, None


def find_name(profile_text: str, description: str) -> str | None:
    """Best-effort candidate name from the description, then the resume text."""
    for text in (description, profile_text):
        if m := _NAME_RE.search(text or ""):
            return m.group(1)[:80]
    for line in (profile_text or "").splitlines():
        line = line.strip()
        if line and "@" not in line and len(line.split()) <= 4 and len(line) <= 60:
            return line
    return None
