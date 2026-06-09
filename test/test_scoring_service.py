"""Tests for app.components.scoring (scoring_service + scoring_tasks)."""

from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.components.scoring import scoring_service
from app.components.scoring.scoring_service import (
    _collect_round,
    _score_mcq,
    calculate_submission_score,
    score_round,
)

# ---------------------------------------------------------------------------
# _score_mcq
# ---------------------------------------------------------------------------


def test_score_mcq_correct_single():
    original = {"options": [{"id": "a", "is_correct": True}, {"id": "b"}]}
    score, is_correct = _score_mcq(original, "a")
    assert score == 1
    assert is_correct is True


def test_score_mcq_wrong():
    original = {"options": [{"id": "a", "is_correct": True}, {"id": "b"}]}
    score, is_correct = _score_mcq(original, "b")
    assert score == 0
    assert is_correct is False


def test_score_mcq_multi_with_objectid_options():
    oid = ObjectId()
    original = {"options": [{"_id": oid, "is_correct": True}, {"id": "x", "is_correct": True}]}
    score, is_correct = _score_mcq(original, [str(oid), "x"])
    assert is_correct is True
    assert score == 1


def test_score_mcq_empty_answer():
    original = {"options": [{"id": "a", "is_correct": True}]}
    score, is_correct = _score_mcq(original, None)
    assert score == 0
    assert is_correct is False


# ---------------------------------------------------------------------------
# _collect_round
# ---------------------------------------------------------------------------


def test_collect_round_mixes_mcq_and_essay():
    rd = {
        "questions": [
            {"id": "q1", "type": "mcq_single"},
            {"id": "q2", "type": "essay", "text": "Explain X"},
        ],
        "answers": {"q1": "a", "q2": "my answer"},
    }
    originals = {
        "q1": {"options": [{"id": "a", "is_correct": True}]},
        "q2": {"correct_answer": "the answer"},
    }
    essays = []
    correct_mcq, total, mcq_results = _collect_round(rd, originals, essays)
    assert correct_mcq == 1
    assert total == 2
    assert mcq_results["q1"]["is_correct"] is True
    assert len(essays) == 1
    assert essays[0][0] == "q2"


def test_collect_round_essay_non_string_answer():
    rd = {
        "questions": [{"id": "q1", "type": "essay"}],
        "answers": {"q1": ["not", "a", "string"]},
    }
    essays = []
    _, total, _ = _collect_round(rd, {}, essays)
    assert total == 1
    assert essays[0][3] == ""  # non-string answer becomes ""


# ---------------------------------------------------------------------------
# _score_essays_batch
# ---------------------------------------------------------------------------


async def test_score_essays_batch_empty():
    assert await scoring_service._score_essays_batch([]) == {}


async def test_score_essays_batch_no_api_key():
    with patch.object(scoring_service.settings, "OPENAI_API_KEY", ""):
        result = await scoring_service._score_essays_batch([("q1", "Q", "expected", "given")])
    assert result["q1"]["is_correct"] is None
    assert "unavailable" in result["q1"]["feedback"].lower()


async def test_score_essays_batch_with_llm():
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[
        0
    ].message.content = '[{"question_id": "q1", "is_correct": true, "feedback": "Good"}]'
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch.object(scoring_service.settings, "OPENAI_API_KEY", "sk-test"):
        with patch("openai.AsyncOpenAI", return_value=fake_client):
            result = await scoring_service._score_essays_batch([("q1", "Q", "expected", "given")])
    assert result["q1"]["is_correct"] is True
    assert result["q1"]["feedback"] == "Good"


async def test_score_essays_batch_llm_error():
    with patch.object(scoring_service.settings, "OPENAI_API_KEY", "sk-test"):
        with patch("openai.AsyncOpenAI", side_effect=RuntimeError("boom")):
            result = await scoring_service._score_essays_batch([("q1", "Q", "expected", "given")])
    assert result["q1"]["is_correct"] is None
    assert "error" in result["q1"]["feedback"].lower()


# ---------------------------------------------------------------------------
# score_round / calculate_submission_score (with fake DB)
# ---------------------------------------------------------------------------


async def _seed_question(db, *, correct_id="a"):
    qid = ObjectId()
    await db.questions.insert_one(
        {
            "_id": qid,
            "question_text": "What?",
            "options": [
                {"id": correct_id, "is_correct": True},
                {"id": "z", "is_correct": False},
            ],
            "correct_answer": "ref",
        }
    )
    return qid


async def test_score_round_mcq(db):
    qid = await _seed_question(db)
    rd = {
        "questions": [{"id": str(qid), "type": "mcq_single"}],
        "answers": {str(qid): "a"},
    }
    result = await score_round(db, rd)
    assert result["score"] == 1
    assert result["total_questions"] == 1
    assert result["percentage"] == 100.0
    assert result["wrong_answer"] == 0


async def test_score_round_empty(db):
    result = await score_round(db, {"questions": [], "answers": {}})
    assert result["total_questions"] == 0
    assert result["percentage"] == 0.0


async def test_score_round_with_essay(db):
    qid = ObjectId()
    await db.questions.insert_one({"_id": qid, "question_text": "Essay?", "correct_answer": "ref"})
    rd = {
        "questions": [{"id": str(qid), "type": "essay"}],
        "answers": {str(qid): "candidate text"},
    }
    result = await score_round(db, rd)
    # No API key → essay scored as not-correct (is_correct None)
    assert result["total_questions"] == 1
    assert str(qid) in result["question_results"]


async def test_calculate_submission_score(db):
    qid = await _seed_question(db)
    sub = {
        "rounds_data": [
            {
                "questions": [{"id": str(qid), "type": "mcq_single"}],
                "answers": {str(qid): "a"},
            }
        ]
    }
    result = await calculate_submission_score(db, sub)
    assert result["scoring_completed"] is True
    assert result["score"] == 1
    assert result["percentage"] == 100.0


async def test_calculate_submission_score_empty(db):
    result = await calculate_submission_score(db, {"rounds_data": []})
    assert result["total_questions"] == 0
    assert result["percentage"] == 0.0
