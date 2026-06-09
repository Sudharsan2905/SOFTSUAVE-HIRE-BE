"""Tests for app.components.export.pdf_service."""

import pytest

from app.components.export import pdf_service
from app.components.export.pdf_service import (
    _build_question_flowables,
    _option_marker_and_color,
    generate_submission_pdf,
)


def test_option_marker_and_color_correct():
    marker, tag = _option_marker_and_color(True, False)
    assert marker == "✓"
    assert "#22c55e" in tag


def test_option_marker_and_color_chosen_wrong():
    marker, tag = _option_marker_and_color(False, True)
    assert marker == "✗"
    assert "#ef4444" in tag


def test_option_marker_and_color_neutral():
    marker, tag = _option_marker_and_color(False, False)
    assert marker == "○"
    assert "#64748b" in tag


def test_build_question_flowables_mcq():
    s = pdf_service._styles()
    q = {
        "id": "q1",
        "question_type": "mcq_single",
        "text": "Pick one",
        "options": [
            {"id": "a", "text": "A", "is_correct": True},
            {"id": "b", "text": "B", "is_correct": False},
        ],
    }
    flow = _build_question_flowables(q, {"q1": ["a"]}, s)
    # prompt + spacer + 2 options
    assert len(flow) >= 3


def test_build_question_flowables_essay_string_answer():
    s = pdf_service._styles()
    q = {"id": "q1", "question_type": "essay", "text": "Explain"}
    flow = _build_question_flowables(q, {"q1": "a long essay answer"}, s)
    assert len(flow) >= 2


def test_generate_submission_pdf_full():
    submission = {
        "status": "completed",
        "score": 8,
        "percentage": 80.0,
        "malpractice_count": 1,
        "reaccess_count": 0,
        "rounds_data": [
            {
                "round_number": 1,
                "score": 8,
                "percentage": 80.0,
                "answers": {"q1": ["a"], "q2": "essay text"},
                "questions": [
                    {
                        "id": "q1",
                        "question_type": "mcq_single",
                        "text": "Q1",
                        "options": [
                            {"id": "a", "text": "A", "is_correct": True},
                            {"id": "b", "text": "B", "is_correct": False},
                        ],
                    },
                    {"id": "q2", "question_type": "essay", "text": "Q2"},
                ],
            }
        ],
        "malpractice_data": [
            {
                "type": "tab_switch",
                "round": 1,
                "timestamp": "2026-06-10T10:00:00",
                "is_terminal": False,
            }
        ],
    }
    candidate = {"first_name": "Jane", "last_name": "Doe", "email": "jane@example.com"}
    pdf_bytes = generate_submission_pdf(submission, candidate, "Backend Assessment")
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")


def test_generate_submission_pdf_minimal():
    pdf_bytes = generate_submission_pdf({}, {}, "Empty")
    assert pdf_bytes.startswith(b"%PDF")


def test_generate_submission_pdf_requires_reportlab(monkeypatch):
    monkeypatch.setattr(pdf_service, "_REPORTLAB_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="reportlab"):
        generate_submission_pdf({}, {}, "x")
