"""Unit tests for app/components/candidate/candidate_service.py"""

import pytest
from bson import ObjectId

from app.common.constants.app_constants import SubmissionStatus
from app.common.exceptions import ForbiddenException, NotFoundException
from app.common.utils import utcnow
from app.components.candidate import candidate_service


@pytest.fixture
async def assessment_doc(db, workspace, super_admin):
    """A minimal assessment with one round."""
    doc = {
        "workspace_id": workspace["_id"],
        "name": "Test Assessment",
        "share_link": "test-link-001",
        "rounds": [
            {
                "round_number": 1,
                "question_count": 1,
                "max_duration_minutes": 30,
                "question_ids": [],
            }
        ],
        "accessibility": "normal",
        "monitoring_config": {"tab_monitoring": True},
        "created_by": super_admin["_id"],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await db.assessments.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture
async def active_submission(db, assessment_doc, candidate_user):
    """An existing IN_PROGRESS submission with an empty round."""
    doc = {
        "assessment_id": assessment_doc["_id"],
        "candidate_id": candidate_user["_id"],
        "status": SubmissionStatus.IN_PROGRESS,
        "current_round": 1,
        "rounds_data": [
            {
                "round_number": 1,
                "question_count": 1,
                "max_duration_minutes": 30,
                "questions": [],
                "answers": {},
                "completed": False,
                "started_at": None,
            }
        ],
        "score": 0,
        "percentage": 0.0,
        "screenshots": [],
        "is_malpractice": False,
        "reaccess_count": 0,
        "started_at": utcnow(),
        "completed_at": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    result = await db.assessment_submissions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


class TestStartAssessment:
    async def test_creates_new_submission(self, db, assessment_doc, candidate_user):
        result = await candidate_service.start_assessment(
            db, "test-link-001", str(candidate_user["_id"])
        )
        assert result["status"] == SubmissionStatus.IN_PROGRESS
        assert "rounds_data" in result

    async def test_returns_existing_in_progress(
        self, db, assessment_doc, candidate_user, active_submission
    ):
        result = await candidate_service.start_assessment(
            db, "test-link-001", str(candidate_user["_id"])
        )
        assert result["id"] == str(active_submission["_id"])

    async def test_completed_submission_raises(
        self, db, assessment_doc, candidate_user, active_submission
    ):
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.COMPLETED}},
        )
        with pytest.raises(ForbiddenException, match="already completed"):
            await candidate_service.start_assessment(
                db, "test-link-001", str(candidate_user["_id"])
            )

    async def test_invalid_share_link_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.start_assessment(
                db, "nonexistent-link", str(candidate_user["_id"])
            )


class TestGetCurrentRound:
    async def test_returns_round_data(self, db, active_submission, candidate_user):
        result = await candidate_service.get_current_round(
            db, str(active_submission["_id"]), str(candidate_user["_id"])
        )
        assert result["round"]["round_number"] == 1
        assert "questions" in result["round"]
        assert "tab_monitoring" in result

    async def test_invalid_submission_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.get_current_round(
                db, str(ObjectId()), str(candidate_user["_id"])
            )


class TestSubmitAnswer:
    async def test_saves_answer(self, db, active_submission, candidate_user):
        result = await candidate_service.submit_answer(
            db,
            str(active_submission["_id"]),
            str(candidate_user["_id"]),
            "q_id_001",
            "Option B",
        )
        assert result["saved"] is True
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert updated["rounds_data"][0]["answers"].get("q_id_001") == "Option B"


class TestFinishRound:
    async def test_completes_single_round_assessment(
        self, db, assessment_doc, candidate_user, active_submission
    ):
        result = await candidate_service.finish_round(
            db, str(active_submission["_id"]), str(candidate_user["_id"])
        )
        assert result["completed"] is True

        final = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert final["status"] == SubmissionStatus.COMPLETED

    async def test_inactive_submission_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.finish_round(db, str(ObjectId()), str(candidate_user["_id"]))

    async def test_assessment_not_found_raises(self, db, candidate_user, active_submission):
        """Deleting the assessment after submission starts → NotFoundException."""
        await db.assessments.delete_one({"share_link": "test-link-001"})
        with pytest.raises(NotFoundException, match="Assessment not found"):
            await candidate_service.finish_round(
                db, str(active_submission["_id"]), str(candidate_user["_id"])
            )

    async def test_multi_round_advance(self, db, workspace, super_admin, candidate_user):
        """When more rounds remain, finish_round returns completed=False and next_round."""
        two_round_assessment = {
            "workspace_id": workspace["_id"],
            "name": "Two Round",
            "share_link": "two-round-link",
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "question_ids": [],
                },
                {
                    "round_number": 2,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "question_ids": [],
                },
            ],
            "accessibility": "normal",
            "monitoring_config": None,
            "created_by": super_admin["_id"],
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await db.assessments.insert_one(two_round_assessment)

        sub = {
            "assessment_id": two_round_assessment["_id"],
            "candidate_id": candidate_user["_id"],
            "status": SubmissionStatus.IN_PROGRESS,
            "current_round": 1,
            "rounds_data": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "questions": [],
                    "answers": {},
                    "completed": False,
                    "started_at": None,
                },
                {
                    "round_number": 2,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "questions": [],
                    "answers": {},
                    "completed": False,
                    "started_at": None,
                },
            ],
            "score": 0,
            "percentage": 0.0,
            "screenshots": [],
            "is_malpractice": False,
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)

        result = await candidate_service.finish_round(
            db, str(res.inserted_id), str(candidate_user["_id"])
        )
        assert result["completed"] is False
        assert result["next_round"] == 2


class TestStartAssessmentPendingResume:
    async def test_pending_submission_resumes(self, db, assessment_doc, candidate_user):
        pending_sub = {
            "assessment_id": assessment_doc["_id"],
            "candidate_id": candidate_user["_id"],
            "status": SubmissionStatus.PENDING,
            "current_round": 1,
            "rounds_data": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 30,
                    "questions": [],
                    "answers": {},
                    "completed": False,
                    "started_at": None,
                }
            ],
            "score": 0,
            "percentage": 0.0,
            "screenshots": [],
            "is_malpractice": False,
            "reaccess_count": 0,
            "started_at": None,
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await db.assessment_submissions.insert_one(pending_sub)
        result = await candidate_service.start_assessment(
            db, "test-link-001", str(candidate_user["_id"])
        )
        assert result["status"] == SubmissionStatus.IN_PROGRESS


class TestStartAssessmentWithQuestions:
    async def test_creates_submission_with_questions(
        self, db, workspace, super_admin, candidate_user, category
    ):
        """Cover the question-sampling path when question_ids are provided."""
        # Insert a question
        q_res = await db.questions.insert_one(
            {
                "category_id": category["_id"],
                "question_text": "What is 1+1?",
                "question_type": "mcq_single",
                "complexity": "low",
                "options": [{"id": "a", "text": "2", "is_correct": True}],
                "correct_answer": None,
                "created_by": super_admin["_id"],
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )
        assessment_doc = {
            "workspace_id": workspace["_id"],
            "name": "Q Assessment",
            "share_link": "q-link-001",
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 30,
                    "question_ids": [q_res.inserted_id],
                },
            ],
            "accessibility": "normal",
            "monitoring_config": {"tab_monitoring": True},
            "created_by": super_admin["_id"],
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await db.assessments.insert_one(assessment_doc)

        result = await candidate_service.start_assessment(
            db, "q-link-001", str(candidate_user["_id"])
        )
        assert result["status"] == SubmissionStatus.IN_PROGRESS
        assert len(result["rounds_data"]) == 1


class TestGetCurrentRoundEdgeCases:
    async def test_round_not_found(self, db, candidate_user, active_submission):
        """When current_round index exceeds rounds_data length."""
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]}, {"$set": {"current_round": 99}}
        )
        with pytest.raises(NotFoundException, match="Round not found"):
            await candidate_service.get_current_round(
                db, str(active_submission["_id"]), str(candidate_user["_id"])
            )


class TestSubmitAnswerNotFound:
    async def test_no_active_submission_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.submit_answer(
                db, str(ObjectId()), str(candidate_user["_id"]), "q1", "answer"
            )


class TestSaveScreenshot:
    async def test_saves_screenshot(self, db, active_submission, candidate_user):
        await candidate_service.save_screenshot(
            db, str(active_submission["_id"]), str(candidate_user["_id"]), b"fake_image_bytes"
        )
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert len(updated["screenshots"]) == 1

    async def test_no_submission_silently_returns(self, db, candidate_user):
        """save_screenshot returns silently when submission not found."""
        await candidate_service.save_screenshot(
            db, str(ObjectId()), str(candidate_user["_id"]), b"bytes"
        )


class TestFlagMalpractice:
    async def test_flags_malpractice(self, db, active_submission, candidate_user, assessment_doc):
        await candidate_service.flag_malpractice(
            db, str(active_submission["_id"]), str(candidate_user["_id"]), "tab_switch"
        )
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert updated["status"] == SubmissionStatus.MALPRACTICE

    async def test_no_tab_monitoring_skips_flag(self, db, workspace, super_admin, candidate_user):
        """When tab_monitoring is False, malpractice flag is skipped."""
        assessment = {
            "workspace_id": workspace["_id"],
            "name": "No Monitor",
            "share_link": "no-mon-link",
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 30,
                    "question_ids": [],
                }
            ],
            "accessibility": "normal",
            "monitoring_config": {"tab_monitoring": False},
            "created_by": super_admin["_id"],
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await db.assessments.insert_one(assessment)

        sub = {
            "assessment_id": assessment["_id"],
            "candidate_id": candidate_user["_id"],
            "status": SubmissionStatus.IN_PROGRESS,
            "current_round": 1,
            "rounds_data": [],
            "score": 0,
            "percentage": 0.0,
            "screenshots": [],
            "is_malpractice": False,
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)

        await candidate_service.flag_malpractice(
            db, str(res.inserted_id), str(candidate_user["_id"]), "tab_switch"
        )
        updated = await db.assessment_submissions.find_one({"_id": res.inserted_id})
        assert updated["status"] == SubmissionStatus.IN_PROGRESS

    async def test_not_found_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.flag_malpractice(
                db, str(ObjectId()), str(candidate_user["_id"]), "tab_switch"
            )


class TestGetCandidateAssessment:
    async def test_success(self, db, assessment_doc):
        result = await candidate_service.get_candidate_assessment(db, "test-link-001")
        assert result["name"] == "Test Assessment"
        for r in result.get("rounds", []):
            assert "question_ids" not in r

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await candidate_service.get_candidate_assessment(db, "bad-link")


class TestGetLiveInterviews:
    async def test_returns_paginated(self, db, assessment_doc, candidate_user):
        await db.assessment_submissions.insert_one(
            {
                "assessment_id": assessment_doc["_id"],
                "candidate_id": candidate_user["_id"],
                "status": "in_progress",
                "current_round": 1,
                "rounds_data": [],
                "score": 0,
                "percentage": 0.0,
                "screenshots": [],
                "is_malpractice": False,
                "reaccess_count": 0,
                "started_at": utcnow(),
                "completed_at": None,
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )
        result = await candidate_service.get_live_interviews(
            db, None, None, "started_at", "desc", 1, 20
        )
        assert "live_interviews" in result
        assert "pagination" in result

    async def test_with_search_and_monitoring_type(self, db):
        result = await candidate_service.get_live_interviews(
            db, "Test", "normal", "started_at", "asc", 1, 20
        )
        assert "live_interviews" in result


class TestCalculateScoreEssayAndMissing:
    async def test_essay_question_skipped(self, db, workspace, super_admin, candidate_user):
        """Cover line 294: essay questions are skipped (continue)."""
        assessment = {
            "workspace_id": workspace["_id"],
            "name": "Essay Score",
            "share_link": "essay-score-link",
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "question_ids": [],
                }
            ],
            "accessibility": "normal",
            "monitoring_config": None,
            "created_by": super_admin["_id"],
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await db.assessments.insert_one(assessment)

        sub = {
            "assessment_id": assessment["_id"],
            "candidate_id": candidate_user["_id"],
            "status": SubmissionStatus.IN_PROGRESS,
            "current_round": 1,
            "rounds_data": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "questions": [
                        {"id": "essay_q_1", "text": "Explain OOP", "question_type": "essay"}
                    ],
                    "answers": {"essay_q_1": "Some essay answer"},
                    "completed": False,
                    "started_at": None,
                }
            ],
            "score": 0,
            "percentage": 0.0,
            "screenshots": [],
            "is_malpractice": False,
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)
        result = await candidate_service.finish_round(
            db, str(res.inserted_id), str(candidate_user["_id"])
        )
        assert result["completed"] is True

    async def test_missing_question_in_db_skipped(self, db, workspace, super_admin, candidate_user):
        """Cover line 298: if original question not found in DB, skip (continue)."""
        from bson import ObjectId

        nonexistent_id = str(ObjectId())

        assessment = {
            "workspace_id": workspace["_id"],
            "name": "Missing Q Score",
            "share_link": "missing-q-link",
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "question_ids": [],
                }
            ],
            "accessibility": "normal",
            "monitoring_config": None,
            "created_by": super_admin["_id"],
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await db.assessments.insert_one(assessment)

        sub = {
            "assessment_id": assessment["_id"],
            "candidate_id": candidate_user["_id"],
            "status": SubmissionStatus.IN_PROGRESS,
            "current_round": 1,
            "rounds_data": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "questions": [{"id": nonexistent_id, "text": "Q?", "type": "mcq_single"}],
                    "answers": {nonexistent_id: "a"},
                    "completed": False,
                    "started_at": None,
                }
            ],
            "score": 0,
            "percentage": 0.0,
            "screenshots": [],
            "is_malpractice": False,
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)
        result = await candidate_service.finish_round(
            db, str(res.inserted_id), str(candidate_user["_id"])
        )
        assert result["completed"] is True


class TestCalculateScore:
    async def test_mcq_scoring(self, db, workspace, super_admin, candidate_user, category):
        """Cover _calculate_score with MCQ questions."""
        q_res = await db.questions.insert_one(
            {
                "category_id": category["_id"],
                "question_text": "Q?",
                "question_type": "mcq_single",
                "complexity": "low",
                "options": [
                    {"id": "a", "text": "Yes", "is_correct": True},
                    {"id": "b", "text": "No", "is_correct": False},
                ],
                "correct_answer": None,
                "created_by": super_admin["_id"],
                "created_at": utcnow(),
                "updated_at": utcnow(),
            }
        )
        assessment = {
            "workspace_id": workspace["_id"],
            "name": "Score Test",
            "share_link": "score-link",
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "question_ids": [q_res.inserted_id],
                }
            ],
            "accessibility": "normal",
            "monitoring_config": None,
            "created_by": super_admin["_id"],
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await db.assessments.insert_one(assessment)

        sub = {
            "assessment_id": assessment["_id"],
            "candidate_id": candidate_user["_id"],
            "status": SubmissionStatus.IN_PROGRESS,
            "current_round": 1,
            "rounds_data": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 10,
                    "questions": [
                        {
                            "id": str(q_res.inserted_id),
                            "text": "Q?",
                            "type": "mcq_single",
                            "options": [],
                        }
                    ],
                    "answers": {str(q_res.inserted_id): "a"},
                    "completed": False,
                    "started_at": None,
                }
            ],
            "score": 0,
            "percentage": 0.0,
            "screenshots": [],
            "is_malpractice": False,
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)

        result = await candidate_service.finish_round(
            db, str(res.inserted_id), str(candidate_user["_id"])
        )
        assert result["completed"] is True
        assert result["percentage"] == 100.0
