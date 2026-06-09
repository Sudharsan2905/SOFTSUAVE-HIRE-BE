"""PDF export service — generates A4 branded submission reports.

Requires: reportlab
    pip install reportlab
"""

from io import BytesIO

from app.core.logging import logger

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    _REPORTLAB_AVAILABLE = True
except ImportError:
    _REPORTLAB_AVAILABLE = False
    logger.warning("reportlab not installed — PDF export disabled. Run: pip install reportlab")

_BRAND_COLOR = colors.HexColor("#6366f1") if _REPORTLAB_AVAILABLE else None
_TEXT_DARK = colors.HexColor("#1e293b") if _REPORTLAB_AVAILABLE else None
_TEXT_MUTED = colors.HexColor("#64748b") if _REPORTLAB_AVAILABLE else None
_BG_LIGHT = colors.HexColor("#f8fafc") if _REPORTLAB_AVAILABLE else None
_SUCCESS = colors.HexColor("#22c55e") if _REPORTLAB_AVAILABLE else None
_ERROR = colors.HexColor("#ef4444") if _REPORTLAB_AVAILABLE else None


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            parent=base["Heading1"],
            fontSize=20,
            textColor=_BRAND_COLOR,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontSize=11,
            textColor=_TEXT_MUTED,
            spaceAfter=12,
        ),
        "section_head": ParagraphStyle(
            "section_head",
            parent=base["Heading2"],
            fontSize=13,
            textColor=_TEXT_DARK,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontSize=10,
            textColor=_TEXT_DARK,
            leading=15,
        ),
        "label": ParagraphStyle(
            "label",
            parent=base["Normal"],
            fontSize=9,
            textColor=_TEXT_MUTED,
        ),
        "answer": ParagraphStyle(
            "answer",
            parent=base["Normal"],
            fontSize=10,
            textColor=_TEXT_DARK,
            leftIndent=12,
            leading=14,
        ),
    }


def _option_marker_and_color(is_correct: bool, is_chosen: bool) -> tuple[str, str]:
    """Return (marker, font-open-tag) for an MCQ option row in the PDF."""
    if is_correct:
        return "✓", '<font color="#22c55e">'
    if is_chosen:
        return "✗", '<font color="#ef4444">'
    return "○", '<font color="#64748b">'


def _build_question_flowables(q: dict, answers: dict, s: dict) -> list:
    """Render one question (prompt + MCQ options or essay answer) to flowables."""
    qid = str(q.get("_id", q.get("id", "")))
    q_text = q.get("question_text", q.get("text", ""))
    q_type = q.get("question_type", "")
    candidate_answer = answers.get(qid, [])
    if isinstance(candidate_answer, str):
        candidate_answer = [candidate_answer]

    flow: list = [Spacer(1, 0.2 * cm), Paragraph(f"Q: {q_text}", s["body"])]

    if q_type in ("mcq_single", "mcq_multi"):
        for opt in q.get("options", []):
            opt_text = opt.get("text", "")
            is_correct = opt.get("is_correct", False)
            is_chosen = opt.get("id", "") in candidate_answer
            marker, color_tag = _option_marker_and_color(is_correct, is_chosen)
            flow.append(Paragraph(f"{color_tag}{marker} {opt_text}</font>", s["answer"]))
    elif q_type == "essay" and candidate_answer:
        flow.append(Paragraph(f"Answer: {candidate_answer[0][:500]}", s["answer"]))

    return flow


def _build_round_flowables(round_data: dict, s: dict) -> list:
    """Render one round header plus all of its questions to flowables."""
    rn = round_data.get("round_number", "?")
    r_score = round_data.get("score", 0)
    r_pct = round_data.get("percentage", 0.0)

    flow: list = [
        HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey),
        Paragraph(f"Round {rn} — Score: {r_score} ({r_pct:.1f}%)", s["section_head"]),
    ]
    answers = round_data.get("answers", {})
    for q in round_data.get("questions", []):
        flow.extend(_build_question_flowables(q, answers, s))
    flow.append(Spacer(1, 0.3 * cm))
    return flow


def _build_info_table(submission: dict, candidate: dict, assessment_name: str) -> "Table":
    """Build the candidate/assessment summary table."""
    full_name = f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}".strip()
    info_data = [
        ["Candidate", full_name, "Assessment", assessment_name],
        [
            "Email",
            candidate.get("email", "—"),
            "Status",
            submission.get("status", "—").replace("_", " ").title(),
        ],
        [
            "Score",
            f"{submission.get('score', 0)} pts",
            "Percentage",
            f"{submission.get('percentage', 0):.1f}%",
        ],
        [
            "Malpractice Events",
            str(submission.get("malpractice_count", 0)),
            "Re-access Count",
            str(submission.get("reaccess_count", 0)),
        ],
    ]
    info_table = Table(info_data, colWidths=[3.5 * cm, 7 * cm, 3.5 * cm, 3 * cm])
    info_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (0, -1), _TEXT_MUTED),
                ("TEXTCOLOR", (2, 0), (2, -1), _TEXT_MUTED),
                ("TEXTCOLOR", (1, 0), (1, -1), _TEXT_DARK),
                ("TEXTCOLOR", (3, 0), (3, -1), _TEXT_DARK),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [_BG_LIGHT, colors.white]),
            ]
        )
    )
    return info_table


def _build_malpractice_table(mal_events: list) -> "Table":
    """Build the malpractice-events table from a list of event dicts."""
    mal_rows = [["#", "Type", "Round", "Timestamp", "Terminal"]]
    for i, ev in enumerate(mal_events, 1):
        mal_rows.append(
            [
                str(i),
                ev.get("type", "").replace("_", " ").title(),
                str(ev.get("round", "—")),
                str(ev.get("timestamp", "—"))[:19],
                "Yes" if ev.get("is_terminal") else "No",
            ]
        )
    mal_table = Table(mal_rows, colWidths=[1 * cm, 5 * cm, 2 * cm, 5 * cm, 2 * cm])
    mal_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _ERROR),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_BG_LIGHT, colors.white]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return mal_table


def generate_submission_pdf(submission: dict, candidate: dict, assessment_name: str) -> bytes:
    """Render a single-submission A4 PDF report and return raw bytes.

    Args:
        submission: Serialized submission document (rounds_data, malpractice_data, etc.)
        candidate: Candidate profile dict (first_name, last_name, email, …)
        assessment_name: Display name of the assessment

    Returns:
        PDF bytes ready to stream as a response.

    Raises:
        RuntimeError: If reportlab is not installed.
    """
    if not _REPORTLAB_AVAILABLE:
        raise RuntimeError("PDF export requires reportlab. Install it with: pip install reportlab")

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    s = _styles()
    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph("SoftSuave Hire", s["title"]))
    story.append(Paragraph("Candidate Assessment Report", s["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=_BRAND_COLOR))
    story.append(Spacer(1, 0.3 * cm))

    # ── Candidate & Assessment Info ───────────────────────────────────────────
    story.append(_build_info_table(submission, candidate, assessment_name))
    story.append(Spacer(1, 0.4 * cm))

    # ── Rounds ────────────────────────────────────────────────────────────────
    for round_data in submission.get("rounds_data", []):
        story.extend(_build_round_flowables(round_data, s))

    # ── Malpractice Events ────────────────────────────────────────────────────
    mal_events = submission.get("malpractice_data", [])
    if mal_events:
        story.append(HRFlowable(width="100%", thickness=0.5, color=_ERROR))
        story.append(Paragraph("Malpractice Events", s["section_head"]))
        story.append(_build_malpractice_table(mal_events))

    doc.build(story)
    return buf.getvalue()
