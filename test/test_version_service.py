"""Unit tests for app/components/version_history/version_service.py"""

from unittest.mock import AsyncMock

import pytest

from app.components.version_history import version_service


@pytest.fixture(autouse=True)
def _patch_s3(monkeypatch):
    """Make S3 deterministic: presign returns a fixed URL."""
    monkeypatch.setattr(
        version_service.s3_service,
        "get_presigned_url",
        AsyncMock(return_value="https://signed.example/x"),
    )


class TestBuildQuestionAnswer:
    def test_carries_image_key_from_original(self):
        q = {"id": "q1", "text": "Pick the diagram"}
        original = {
            "question_type": "mcq_single",
            "question_text": "Pick the diagram",
            "image_key": "question-images/cat1/q.jpg",
            "options": [
                {"id": "a", "text": "", "is_correct": True, "image_key": "question-images/cat1/a.jpg"},
                {"id": "b", "text": "Option B", "is_correct": False},
            ],
        }
        qa = version_service._build_question_answer(q, original, ["a"])
        assert qa["image_key"] == "question-images/cat1/q.jpg"
        options_by_id = {o["id"]: o for o in qa["options"]}
        assert options_by_id["a"]["image_key"] == "question-images/cat1/a.jpg"
        assert options_by_id["b"]["image_key"] is None


class TestBuildQuestionAnswerFromEmbedded:
    def test_prefers_embedded_image_key_falls_back_to_original(self):
        q = {
            "id": "q1",
            "text": "Pick the diagram",
            "type": "mcq_single",
            "image_key": "question-images/cat1/q.jpg",
            "options": [
                {"id": "a", "text": "", "image_key": "question-images/cat1/a.jpg"},
                {"id": "b", "text": "Option B"},
            ],
        }
        original = {
            "options": [
                {"id": "a", "is_correct": True, "image_key": "question-images/cat1/original-a.jpg"},
                {"id": "b", "is_correct": False, "image_key": "question-images/cat1/original-b.jpg"},
            ]
        }
        qa = version_service._build_question_answer_from_embedded(q, original, ["a"], {})
        assert qa["image_key"] == "question-images/cat1/q.jpg"
        options_by_id = {o["id"]: o for o in qa["options"]}
        # Embedded option "a" already had its own image_key -> keep it.
        assert options_by_id["a"]["image_key"] == "question-images/cat1/a.jpg"
        # Embedded option "b" had none -> fall back to the original's.
        assert options_by_id["b"]["image_key"] == "question-images/cat1/original-b.jpg"


class TestResolveRoundQuestionImages:
    async def test_replaces_image_key_with_presigned_url(self):
        rounds = [
            {
                "round_number": 1,
                "question_answers": [
                    {
                        "question_id": "q1",
                        "image_key": "question-images/cat1/q.jpg",
                        "options": [
                            {"id": "a", "image_key": "question-images/cat1/a.jpg"},
                            {"id": "b", "image_key": None},
                        ],
                    }
                ],
            }
        ]
        resolved = await version_service._resolve_round_question_images(rounds)
        qa = resolved[0]["question_answers"][0]
        assert qa["image_url"] == "https://signed.example/x"
        assert "image_key" not in qa
        options_by_id = {o["id"]: o for o in qa["options"]}
        assert options_by_id["a"]["image_url"] == "https://signed.example/x"
        assert "image_key" not in options_by_id["a"]
        assert options_by_id["b"]["image_url"] is None
