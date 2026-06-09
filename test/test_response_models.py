"""Instantiation coverage for response-model / schema modules that hold pure
Pydantic definitions (submission_responses, proctoring/version schemas, types)."""

from datetime import datetime

from app.common.constants.app_constants import MalpracticeType, SubmissionStatus
from app.common.constants.types import MongoDocument, PaginatedDocs
from app.common.response_models.submission_responses import (
    CandidateSubmissionResponse,
    MalpracticeEventResponse,
    QuestionAnswerResponse,
    QuestionOptionResponse,
    RoundDataResponse,
    ScreenshotResponse,
    VersionSummaryResponse,
)
from app.common.response_models.user_responses import CandidateProfileResponse
from app.components.proctoring.proctoring_schemas import (
    MalpracticeEvidenceResult,
    ProctoringEvent,
)
from app.components.version_history.version_schemas import VersionHistoryListResponse


def test_type_aliases_importable():
    assert MongoDocument is not None
    assert PaginatedDocs is not None


def test_submission_response_models_build():
    now = datetime(2026, 6, 10, 10, 0, 0)
    opt = QuestionOptionResponse(id="a", text="A", is_correct=True)
    qa = QuestionAnswerResponse(
        question_id="q1",
        question_text="Q?",
        question_type="mcq_single",
        options=[opt],
        candidate_answer=["a"],
        is_correct=True,
    )
    rd = RoundDataResponse(round_number=1, score=5, percentage=50.0, question_answers=[qa])
    mal = MalpracticeEventResponse(
        type=MalpracticeType.TAB_SWITCH, timestamp=now, round=1, is_terminal=True
    )
    shot = ScreenshotResponse(url="http://x", round=1, taken_at=now)
    ver = VersionSummaryResponse(version=1, status="completed", percentage=80.0)

    candidate = CandidateProfileResponse(
        id="c1",
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
        created_at=now,
    )
    sub = CandidateSubmissionResponse(
        candidate=candidate,
        submission_id="s1",
        status=SubmissionStatus.COMPLETED,
        score=8,
        percentage=80.0,
        malpractice_count=1,
        reaccess_count=0,
        current_version=1,
        available_versions=[ver],
        rounds=[rd],
        malpractice_events=[mal],
        screenshots=[shot],
    )
    assert sub.submission_id == "s1"
    assert sub.rounds[0].question_answers[0].options[0].id == "a"


def test_proctoring_schemas_build():
    res = MalpracticeEvidenceResult(screen_image_s3_key="k")
    assert res.screen_image_s3_key == "k"
    ev = ProctoringEvent(submission_id="s1", malpractice_type=MalpracticeType.COPY_PASTE, round=2)
    assert ev.round == 2


def test_version_schema_build():
    v = VersionHistoryListResponse(
        version=1, status="completed", score=5, percentage=50.0, malpractice_count=0
    )
    assert v.version == 1
