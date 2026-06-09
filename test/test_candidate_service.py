"""Unit tests for app/components/candidate/candidate_service.py"""

from unittest.mock import AsyncMock, MagicMock, patch

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
        "is_active": True,
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
        "share_id": None,
        "monitoring_overrides": None,
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
        "malpractice_count": 0,
        "malpractice_events": [],
        "reaccess_count": 0,
        "remaining_seconds": None,
        "current_question_idx": 0,
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

    async def test_on_hold_submission_returned_as_is(
        self, db, assessment_doc, candidate_user, active_submission
    ):
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.ON_HOLD}},
        )
        result = await candidate_service.start_assessment(
            db, "test-link-001", str(candidate_user["_id"])
        )
        assert result["status"] == SubmissionStatus.ON_HOLD

    async def test_malpractice_submission_raises(
        self, db, assessment_doc, candidate_user, active_submission
    ):
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.MALPRACTICE}},
        )
        with pytest.raises(ForbiddenException):
            await candidate_service.start_assessment(
                db, "test-link-001", str(candidate_user["_id"])
            )


class TestGetCurrentRound:
    async def test_returns_round_data(self, db, active_submission, candidate_user):
        result = await candidate_service.get_current_round(
            db, str(active_submission["_id"]), str(candidate_user["_id"])
        )
        assert result["round"]["round_number"] == 1
        assert "questions" in result["round"]
        assert "tab_monitoring" in result
        assert "audio_monitoring" in result
        assert "video_monitoring" in result
        assert "session_status" in result

    async def test_invalid_submission_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.get_current_round(
                db, str(ObjectId()), str(candidate_user["_id"])
            )

    async def test_completed_submission_raises(self, db, active_submission, candidate_user):
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.COMPLETED}},
        )
        with pytest.raises(ForbiddenException, match="already been completed"):
            await candidate_service.get_current_round(
                db, str(active_submission["_id"]), str(candidate_user["_id"])
            )

    async def test_terminated_submission_raises(self, db, active_submission, candidate_user):
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.TERMINATED}},
        )
        with pytest.raises(ForbiddenException, match="terminated"):
            await candidate_service.get_current_round(
                db, str(active_submission["_id"]), str(candidate_user["_id"])
            )

    async def test_on_hold_returns_data(self, db, active_submission, candidate_user):
        """ON_HOLD submissions should still return round data for UI restore."""
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.ON_HOLD}},
        )
        result = await candidate_service.get_current_round(
            db, str(active_submission["_id"]), str(candidate_user["_id"])
        )
        assert result["session_status"] == SubmissionStatus.ON_HOLD


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


class TestSubmitAnswerNotFound:
    async def test_no_active_submission_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.submit_answer(
                db, str(ObjectId()), str(candidate_user["_id"]), "q1", "answer"
            )


class TestFinishRound:
    async def test_completes_single_round_assessment(
        self, db, assessment_doc, candidate_user, active_submission
    ):
        # array_filters in update_one are not supported by mongomock — patch at class level
        with patch("mongomock.collection.Collection.update_one", new=MagicMock(return_value=None)):
            result = await candidate_service.finish_round(
                db, str(active_submission["_id"]), str(candidate_user["_id"])
            )
        assert result["completed"] is True
        assert result["finished_round"] == 1

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
            "is_active": True,
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
            "malpractice_count": 0,
            "malpractice_events": [],
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)

        with patch("mongomock.collection.Collection.update_one", new=MagicMock(return_value=None)):
            result = await candidate_service.finish_round(
                db, str(res.inserted_id), str(candidate_user["_id"])
            )
        assert result["completed"] is False
        assert result["next_round"] == 2
        assert result["finished_round"] == 1


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
            "malpractice_count": 0,
            "malpractice_events": [],
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
            "is_active": True,
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


class TestSaveScreenshot:
    async def test_saves_screenshot(self, db, active_submission, candidate_user):
        with patch("app.components.candidate.candidate_service.s3_service") as mock_s3:
            mock_s3.make_screenshot_key.return_value = "screenshots/fake/key.jpg"
            mock_s3.upload = AsyncMock()
            await candidate_service.save_screenshot(
                db,
                str(active_submission["_id"]),
                str(candidate_user["_id"]),
                b"fake_image_bytes",
            )
            mock_s3.upload.assert_called_once()

        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert len(updated["screenshots"]) == 1

    async def test_no_submission_silently_returns(self, db, candidate_user):
        """save_screenshot returns silently when submission not found."""
        with patch("app.components.candidate.candidate_service.s3_service"):
            await candidate_service.save_screenshot(
                db, str(ObjectId()), str(candidate_user["_id"]), b"bytes"
            )


class TestFlagMalpractice:
    async def test_increments_malpractice_count(self, db, active_submission, candidate_user):
        """One strike should increment count without terminating (MAX is 3)."""
        result = await candidate_service.flag_malpractice(
            db, str(active_submission["_id"]), str(candidate_user["_id"]), "tab_switch"
        )
        assert result["malpractice_count"] == 1
        assert result["is_terminal"] is False
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert updated["status"] == SubmissionStatus.IN_PROGRESS

    async def test_terminal_on_third_strike(self, db, active_submission, candidate_user):
        """Third strike (malpractice_count reaches 3) terminates the submission."""
        # Pre-set count to 2 so next call reaches the limit
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"malpractice_count": 2}},
        )
        result = await candidate_service.flag_malpractice(
            db, str(active_submission["_id"]), str(candidate_user["_id"]), "tab_switch"
        )
        assert result["is_terminal"] is True
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert updated["status"] == SubmissionStatus.MALPRACTICE

    async def test_video_event_skipped_when_video_monitoring_disabled(
        self, db, workspace, super_admin, candidate_user
    ):
        """VIDEO events (face_absence) are skipped when video_monitoring=False."""
        assessment = {
            "workspace_id": workspace["_id"],
            "name": "No Video Monitor",
            "share_link": "no-video-link",
            "is_active": True,
            "rounds": [
                {
                    "round_number": 1,
                    "question_count": 1,
                    "max_duration_minutes": 30,
                    "question_ids": [],
                }
            ],
            "accessibility": "normal",
            "monitoring_config": {"tab_monitoring": True, "video_monitoring": False},
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
            "malpractice_count": 0,
            "malpractice_events": [],
            "score": 0,
            "percentage": 0.0,
            "screenshots": [],
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)

        result = await candidate_service.flag_malpractice(
            db, str(res.inserted_id), str(candidate_user["_id"]), "face_absence"
        )
        # Should be silently skipped — count unchanged
        assert result["malpractice_count"] == 0
        assert result["is_terminal"] is False
        updated = await db.assessment_submissions.find_one({"_id": res.inserted_id})
        assert updated["status"] == SubmissionStatus.IN_PROGRESS

    async def test_screen_event_goes_through_regardless_of_tab_monitoring(
        self, db, workspace, super_admin, candidate_user
    ):
        """Screen behavioral events (tab_switch) always go through, ignoring tab_monitoring."""
        assessment = {
            "workspace_id": workspace["_id"],
            "name": "Tab Monitor Off",
            "share_link": "tab-off-link",
            "is_active": True,
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
            "malpractice_count": 0,
            "malpractice_events": [],
            "score": 0,
            "percentage": 0.0,
            "screenshots": [],
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)

        result = await candidate_service.flag_malpractice(
            db, str(res.inserted_id), str(candidate_user["_id"]), "tab_switch"
        )
        # Screen events always go through regardless of tab_monitoring
        assert result["malpractice_count"] == 1

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


class TestGetSubmissionStatus:
    async def test_returns_none_when_no_submission(self, db, assessment_doc, candidate_user):
        result = await candidate_service.get_submission_status(
            db, "test-link-001", str(candidate_user["_id"])
        )
        assert result is None

    async def test_returns_status_when_exists(
        self, db, assessment_doc, candidate_user, active_submission
    ):
        result = await candidate_service.get_submission_status(
            db, "test-link-001", str(candidate_user["_id"])
        )
        assert result is not None
        assert result["status"] == SubmissionStatus.IN_PROGRESS
        assert "submission_id" in result

    async def test_invalid_share_link_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.get_submission_status(
                db, "nonexistent-link", str(candidate_user["_id"])
            )


class TestGetSessionState:
    async def test_returns_state(self, db, active_submission, candidate_user):
        result = await candidate_service.get_session_state(
            db, str(active_submission["_id"]), str(candidate_user["_id"])
        )
        assert "status" in result
        assert "current_round" in result
        assert "remaining_seconds" in result
        assert "current_question_idx" in result

    async def test_not_found_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.get_session_state(
                db, str(ObjectId()), str(candidate_user["_id"])
            )


class TestPutSessionOnHold:
    async def test_marks_in_progress_as_on_hold(self, db, active_submission):
        await candidate_service.put_session_on_hold(db, str(active_submission["_id"]))
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert updated["status"] == SubmissionStatus.ON_HOLD

    async def test_already_on_hold_is_idempotent(self, db, active_submission):
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.ON_HOLD}},
        )
        await candidate_service.put_session_on_hold(db, str(active_submission["_id"]))
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert updated["status"] == SubmissionStatus.ON_HOLD


class TestPutSessionTerminated:
    async def test_terminates_in_progress_submission(self, db, active_submission, candidate_user):
        round_num = await candidate_service.put_session_terminated(
            db, str(active_submission["_id"]), "Admin forced termination"
        )
        assert round_num == 1
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert updated["status"] == SubmissionStatus.TERMINATED

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await candidate_service.put_session_terminated(db, str(ObjectId()), "reason")

    async def test_already_completed_raises(self, db, active_submission):
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.COMPLETED}},
        )
        with pytest.raises(ForbiddenException):
            await candidate_service.put_session_terminated(
                db, str(active_submission["_id"]), "reason"
            )


class TestPutSessionCompleted:
    async def test_completes_in_progress_submission(self, db, active_submission, candidate_user):
        round_num = await candidate_service.put_session_completed(db, str(active_submission["_id"]))
        assert round_num == 1
        updated = await db.assessment_submissions.find_one({"_id": active_submission["_id"]})
        assert updated["status"] == SubmissionStatus.COMPLETED

    async def test_not_found_raises(self, db):
        with pytest.raises(NotFoundException):
            await candidate_service.put_session_completed(db, str(ObjectId()))

    async def test_already_terminated_raises(self, db, active_submission):
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {"$set": {"status": SubmissionStatus.TERMINATED}},
        )
        with pytest.raises(ForbiddenException):
            await candidate_service.put_session_completed(db, str(active_submission["_id"]))


class TestUploadMalpracticeMedia:
    async def test_invalid_event_index_raises(self, db, active_submission, candidate_user):
        from app.common.exceptions import AppException

        with pytest.raises(AppException, match="Invalid event_index"):
            await candidate_service.upload_malpractice_media(
                db,
                str(active_submission["_id"]),
                0,  # no events exist yet
                str(candidate_user["_id"]),
                video_bytes=b"fake_video",
            )

    async def test_not_found_raises(self, db, candidate_user):
        with pytest.raises(NotFoundException):
            await candidate_service.upload_malpractice_media(
                db, str(ObjectId()), 0, str(candidate_user["_id"])
            )

    async def test_uploads_video_clip(self, db, active_submission, candidate_user):
        """Upload video to an existing malpractice event updates the s3_key field."""
        # Pre-insert a malpractice event
        await db.assessment_submissions.update_one(
            {"_id": active_submission["_id"]},
            {
                "$push": {
                    "malpractice_events": {
                        "type": "tab_switch",
                        "round": 1,
                        "timestamp": utcnow(),
                        "screen_video_s3_key": None,
                        "audio_clip_s3_key": None,
                    }
                }
            },
        )
        with patch("app.components.candidate.candidate_service.s3_service") as mock_s3:
            mock_s3.make_evidence_key.return_value = "evidence/fake/video.webm"
            mock_s3.upload = AsyncMock()
            await candidate_service.upload_malpractice_media(
                db,
                str(active_submission["_id"]),
                0,
                str(candidate_user["_id"]),
                video_bytes=b"fake_video_bytes",
            )
            mock_s3.upload.assert_called_once()


class TestGetLiveInterviews:
    async def test_returns_paginated(self, db):
        # $trim in the aggregation pipeline is not supported by mongomock when documents exist.
        # Test with no documents to verify the result structure without triggering $trim.
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


class TestFinishRoundEssayAndMissing:
    async def test_essay_question_completes_round(self, db, workspace, super_admin, candidate_user):
        """Essay questions don't block round completion."""
        assessment = {
            "workspace_id": workspace["_id"],
            "name": "Essay Score",
            "share_link": "essay-score-link",
            "is_active": True,
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
            "malpractice_count": 0,
            "malpractice_events": [],
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)
        with patch("mongomock.collection.Collection.update_one", new=MagicMock(return_value=None)):
            result = await candidate_service.finish_round(
                db, str(res.inserted_id), str(candidate_user["_id"])
            )
        assert result["completed"] is True

    async def test_missing_question_in_db_completes_round(
        self, db, workspace, super_admin, candidate_user
    ):
        """Missing question in DB does not block round completion."""
        nonexistent_id = str(ObjectId())

        assessment = {
            "workspace_id": workspace["_id"],
            "name": "Missing Q Score",
            "share_link": "missing-q-link",
            "is_active": True,
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
            "malpractice_count": 0,
            "malpractice_events": [],
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)
        with patch("mongomock.collection.Collection.update_one", new=MagicMock(return_value=None)):
            result = await candidate_service.finish_round(
                db, str(res.inserted_id), str(candidate_user["_id"])
            )
        assert result["completed"] is True


class TestFinishRoundMCQ:
    async def test_mcq_round_completes(self, db, workspace, super_admin, candidate_user, category):
        """MCQ round with answered question completes; scoring is a background task."""
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
            "is_active": True,
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
            "malpractice_count": 0,
            "malpractice_events": [],
            "reaccess_count": 0,
            "started_at": utcnow(),
            "completed_at": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        res = await db.assessment_submissions.insert_one(sub)

        with patch("mongomock.collection.Collection.update_one", new=MagicMock(return_value=None)):
            result = await candidate_service.finish_round(
                db, str(res.inserted_id), str(candidate_user["_id"])
            )
        # finish_round returns completed status; scoring is dispatched as a background task
        assert result["completed"] is True
        assert result["finished_round"] == 1
