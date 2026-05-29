from fastapi import APIRouter, Depends, File, Query, UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.responses import success_response
from app.components.auth.auth_dependencies import get_current_user, require_admin
from app.components.candidate import candidate_service
from app.components.candidate.candidate_schemas import (
    MalpracticeRequest,
    SubmitAnswerRequest,
)
from app.core.dependencies import get_db

router = APIRouter()


@router.get("/assessment/{share_link}")
async def get_assessment(share_link: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    result = await candidate_service.get_candidate_assessment(db, share_link)
    return success_response("Assessment retrieved", result)


@router.post("/assessment/{share_link}/start")
async def start_assessment(
    share_link: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await candidate_service.start_assessment(db, share_link, current_user["_id"])
    return success_response("Assessment started", result)


@router.get("/submission/{submission_id}/round")
async def get_current_round(
    submission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await candidate_service.get_current_round(db, submission_id, current_user["_id"])
    return success_response("Round retrieved", result)


@router.post("/submission/{submission_id}/answer")
async def submit_answer(
    submission_id: str,
    request: SubmitAnswerRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await candidate_service.submit_answer(
        db, submission_id, current_user["_id"], request.question_id, request.answer
    )
    return success_response("Answer saved", result)


@router.post("/submission/{submission_id}/finish-round")
async def finish_round(
    submission_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await candidate_service.finish_round(db, submission_id, current_user["_id"])
    return success_response("Round finished", result)


@router.post("/submission/{submission_id}/screenshot")
async def save_screenshot(
    submission_id: str,
    file: UploadFile = File(...),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    content = await file.read()
    await candidate_service.save_screenshot(db, submission_id, current_user["_id"], content)
    return success_response("Screenshot saved")


@router.post("/submission/{submission_id}/malpractice")
async def flag_malpractice(
    submission_id: str,
    request: MalpracticeRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await candidate_service.flag_malpractice(db, submission_id, current_user["_id"], request.type)
    return success_response("Activity flagged")


@router.get("/live-interviews")
async def get_live_interviews(
    search: str = Query(None),
    monitoring_type: str = Query(None),
    sort_by: str = Query("started_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(require_admin),
):
    result = await candidate_service.get_live_interviews(
        db, search, monitoring_type, sort_by, sort_order, page, page_size
    )
    return success_response("Live interviews retrieved", result)
