from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.core.dependencies import get_db
from app.components.auth.auth_dependencies import require_admin, get_current_user
from app.components.candidate import candidate_service
from app.components.candidate.candidate_schemas import (
    SubmitAnswerRequest,
    FinishRoundRequest,
    ScreenshotRequest,
    MalpracticeRequest,
)
from app.common.responses import success_response

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


@router.post("/assessment/{share_link}/submit-answer")
async def submit_answer(
    share_link: str,
    request: SubmitAnswerRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await candidate_service.submit_answer(
        db, share_link, current_user["_id"],
        request.question_id, request.answer, request.round_number
    )
    return success_response("Answer saved", result)


@router.post("/assessment/{share_link}/finish-round")
async def finish_round(
    share_link: str,
    request: FinishRoundRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await candidate_service.finish_round(
        db, share_link, current_user["_id"], request.round_number
    )
    return success_response("Round finished", result)


@router.post("/assessment/{share_link}/screenshot")
async def save_screenshot(
    share_link: str,
    request: ScreenshotRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await candidate_service.save_screenshot(
        db, share_link, current_user["_id"], request.screenshot_data, request.round_number
    )
    return success_response("Screenshot saved")


@router.post("/assessment/{share_link}/malpractice")
async def flag_malpractice(
    share_link: str,
    request: MalpracticeRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    await candidate_service.flag_malpractice(
        db, share_link, current_user["_id"], request.reason, request.details
    )
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
