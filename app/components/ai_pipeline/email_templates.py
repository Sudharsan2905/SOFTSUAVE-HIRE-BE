"""HTML for the invite and report emails."""

import html

from app.core.config import settings


def _shell(inner: str) -> str:
    return (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px">'
        '<div style="background:#2563EB;padding:18px 28px;border-radius:8px 8px 0 0">'
        f'<h1 style="color:#fff;margin:0;font-size:20px">{html.escape(settings.APP_NAME)}</h1></div>'
        '<div style="background:#fff;padding:28px;border:1px solid #e2e8f0;'
        f'border-radius:0 0 8px 8px;color:#334155;font-size:15px">{inner}</div></div>'
    )


def exam_url(share_link: str) -> str:
    """Public exam URL (candidate logs in there with the e-mailed credentials)."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/assessment/{share_link}"


def build_invite_html(name: str, title: str, link: str, username: str, password: str) -> str:
    """ONE email: username + temporary password + exam link."""
    return _shell(
        f"<p>Hello {html.escape(name or '')},</p>"
        f"<p>You have been shortlisted for the <b>{html.escape(title)}</b> role and are invited to a "
        "proctored online assessment.</p>"
        "<p><b>Your login details</b></p>"
        f"<p>Username: <b>{html.escape(username)}</b><br/>"
        f"Temporary password: <b style='font-family:monospace'>{html.escape(password)}</b></p>"
        f'<p><a href="{html.escape(link)}" style="background:#2563EB;color:#fff;padding:12px 24px;'
        'border-radius:8px;text-decoration:none;font-weight:600">Start Assessment</a></p>'
        f'<p style="font-size:13px;color:#64748b">Or open this link: <a href="{html.escape(link)}">'
        f"{html.escape(link)}</a></p>"
        "<p>The assessment is strictly monitored (camera, microphone, screen and tab switching). "
        "Please keep your credentials private. This email was sent only to you.</p>"
    )


def _row(index: int, q: dict, res: dict | None) -> str:
    correct = bool(res and res.get("is_correct"))
    fb = html.escape((res or {}).get("feedback") or "")
    text = html.escape((q.get("text") or "")[:300])
    note = f"<br/><i style='color:#64748b'>{fb}</i>" if fb else ""
    colour = "#15803d" if correct else "#b91c1c"
    return (
        f"<tr><td style='padding:6px;border-bottom:1px solid #e2e8f0'>{index}. {text}{note}</td>"
        f"<td style='padding:6px;border-bottom:1px solid #e2e8f0;white-space:nowrap;color:{colour}'>"
        f"{'Correct' if correct else 'Incorrect'}</td></tr>"
    )


def build_report_html(jd_title: str, name: str, sub: dict) -> str:
    """Final result report for one fully evaluated submission."""
    rows: list[str] = []
    for rd in sub.get("rounds_data", []):
        results = rd.get("question_results") or {}
        rows += [_row(i, q, results.get(q.get("id"))) for i, q in enumerate(rd.get("questions", []), 1)]
    note = ""
    if sub.get("status") in ("terminated", "malpractice"):
        note = "<p style='color:#b91c1c'>The assessment was ended early due to a proctoring violation.</p>"
    return _shell(
        f"<p>Hello {html.escape(name or '')}, here is your result for <b>{html.escape(jd_title)}</b>.</p>"
        f"<p style='font-size:18px'>Score: <b>{sub.get('score', 0)}</b> &middot; "
        f"<b>{sub.get('percentage', 0)}%</b></p>{note}"
        f"<table style='width:100%;border-collapse:collapse;font-size:14px'>{''.join(rows)}</table>"
    )
