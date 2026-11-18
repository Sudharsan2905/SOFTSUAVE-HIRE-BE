"""Orchestrates: intake -> analysis -> questions -> assessment -> provisioning -> invites.

Plain asyncio in the background. Every step, per-candidate result and live event is persisted
in Mongo (``pipelines`` / ``pipeline_candidates``) so the UI can poll it. Each stage selects
its work from persisted fields, which makes "re-run from a failed step" trivial.
"""

import asyncio
import hashlib
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.common.exceptions import ConflictException, NotFoundException, ValidationException
from app.common.utils import build_pagination_meta, serialize_doc, utcnow
from app.components.ai_pipeline import contact_extractor, document_parser, file_storage
from app.components.ai_pipeline.agents import analysis_agent, assessment_agent, question_agent
from app.components.ai_pipeline.constants import (
    MAX_EVENTS,
    PIPELINE_CANDIDATES,
    PIPELINES,
    QUESTION_COUNTS,
    STEP_CATALOG,
    STEP_KEYS,
)
from app.components.ai_pipeline.email_templates import exam_url
from app.components.ai_pipeline.invite_service import provision_one, send_invites
from app.core.config import settings
from app.core.logging import logger

_MIN_TEXT = 30
_HIDDEN_CAND = {"profile_text": 0, "profile_text_hash": 0}
_tasks: set[asyncio.Task] = set()


class StepFailureError(Exception):
    def __init__(self, step: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.step, self.message, self.detail = step, message, detail


# ----------------------------------------------------------------------------- persistence helpers
def _new_steps(total: int) -> list[dict]:
    return [
        {**s, "status": "pending", "started_at": None, "finished_at": None,
         "progress": {"done": 0, "total": total}, "detail": None, "error": None}
        for s in STEP_CATALOG
    ]


async def step_update(db: AsyncIOMotorDatabase, pid: ObjectId, key: str, **fields: object) -> None:
    now = utcnow()
    if fields.get("status") == "running":
        fields["started_at"] = now
        fields.setdefault("finished_at", None)
        fields.setdefault("error", None)
    if fields.get("status") in ("done", "failed", "skipped"):
        fields["finished_at"] = now
    await db[PIPELINES].update_one(
        {"_id": pid, "steps.key": key},
        {"$set": {f"steps.$.{k}": v for k, v in fields.items()} | {"updated_at": now}},
    )


async def step_progress(db: AsyncIOMotorDatabase, pid: ObjectId, key: str, inc: int = 1) -> None:
    await db[PIPELINES].update_one(
        {"_id": pid, "steps.key": key}, {"$inc": {"steps.$.progress.done": inc}}
    )


async def log_event(
    db: AsyncIOMotorDatabase, pid: ObjectId, step: str, message: str, level: str = "info"
) -> None:
    """Append to the live activity feed (capped at the last MAX_EVENTS events)."""
    event = {"ts": utcnow().isoformat(), "step": step, "level": level, "message": message}
    await db[PIPELINES].update_one(
        {"_id": pid}, {"$push": {"events": {"$each": [event], "$slice": -MAX_EVENTS}}}
    )


async def _set_candidate(db: AsyncIOMotorDatabase, cid: ObjectId, **fields: object) -> None:
    await db[PIPELINE_CANDIDATES].update_one(
        {"_id": cid}, {"$set": {**fields, "updated_at": utcnow()}}
    )


def _label(cand: dict) -> str:
    return cand.get("name") or cand.get("email") or str(cand["_id"])


def _candidate_doc(pid: ObjectId, c: dict, now: Any) -> dict:
    return {
        "pipeline_id": pid,
        "index": c["index"],
        "name": (c.get("name") or "").strip() or None,
        "email": None,
        "input_email": (c.get("email") or "").strip().lower() or None,
        "email_source": None,
        "phone": c.get("phone"),
        "description": c.get("description") or "",
        "resume_key": c.get("resume_key"),
        "resume_filename": c.get("resume_filename"),
        "photo_key": c.get("photo_key"),
        "profile_text": None,
        "profile_text_hash": None,
        "analysis": None,
        "user_id": None,
        "submission_id": None,
        "status": "queued",
        "invite_status": "pending",
        "invite_error": None,
        "invited_at": None,
        "report_sent_at": None,
        "report_error": None,
        "error": None,
        "failed_step": None,
        "created_at": now,
        "updated_at": now,
    }


# ----------------------------------------------------------------------------- stages
async def _intake_candidate(
    db: AsyncIOMotorDatabase, sem: asyncio.Semaphore, pid: ObjectId, cand: dict
) -> None:
    async with sem:
        try:
            text = ""
            if cand.get("resume_key"):
                data = await file_storage.read(cand["resume_key"])
                text = await asyncio.to_thread(
                    document_parser.extract_text, cand.get("resume_filename") or "resume.pdf", data
                )
            description = cand.get("description") or ""
            email, source = contact_extractor.resolve_email(text, description)
            if not email and cand.get("input_email"):
                email, source = cand["input_email"], "input"
            name = cand.get("name") or contact_extractor.find_name(text, description)
            profile = text.strip() or description.strip()
            if len(profile) < _MIN_TEXT:
                profile = f"Candidate {name or ''} ({email or ''}). No profile details were provided."
            profile = profile[:12000]
            await _set_candidate(
                db, cand["_id"], profile_text=profile, name=name, email=email, email_source=source,
                profile_text_hash=hashlib.sha256(profile.encode()).hexdigest(),
                status="parsed", error=None, failed_step=None,
            )
            if email:
                await log_event(db, pid, "intake", f"{name or email}: email {email} found in {source}")
            else:
                await log_event(db, pid, "intake", f"{name or cand['index']}: no email found", "warn")
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            await _set_candidate(db, cand["_id"], status="failed", failed_step="intake", error=str(detail)[:300])
            await log_event(db, pid, "intake", f"{_label(cand)}: {str(detail)[:200]}", "error")
        finally:
            await step_progress(db, pid, "intake")


async def _analyze_candidate(
    db: AsyncIOMotorDatabase, sem: asyncio.Semaphore, jd_text: str, pid: ObjectId,
    cand: dict, cache: dict[str, dict],
) -> None:
    async with sem:
        try:
            digest = cand["profile_text_hash"]
            cached = digest in cache
            if not cached:
                result = await analysis_agent.analyze_candidate(
                    jd_text, cand["profile_text"], cand.get("description") or ""
                )
                cache[digest] = result.model_dump()
            analysis = cache[digest]
            name = cand.get("name") or analysis.get("name") or (
                cand["email"].split("@")[0] if cand.get("email") else "Candidate"
            )
            await _set_candidate(
                db, cand["_id"], analysis=analysis, name=name, error=None, failed_step=None,
                status="analyzed" if cand.get("email") else "needs_email",
                invite_status="pending" if cand.get("email") else "skipped",
            )
            note = " (reused identical profile)" if cached else ""
            await log_event(db, pid, "analysis", f"{name}: match {analysis['match_score']}%{note}")
        except Exception as exc:
            detail = getattr(exc, "detail", None) or getattr(exc, "message", None) or str(exc)
            await _set_candidate(db, cand["_id"], status="failed", failed_step="analysis", error=str(detail)[:400])
            await log_event(db, pid, "analysis", f"{_label(cand)}: {str(detail)[:240]}", "error")
        finally:
            await step_progress(db, pid, "analysis")


async def _first_error(db: AsyncIOMotorDatabase, query: dict) -> str:
    doc = await db[PIPELINE_CANDIDATES].find_one(query)
    return (doc or {}).get("error") or ""


async def _run_intake(db: AsyncIOMotorDatabase, pid: ObjectId, sem: asyncio.Semaphore) -> None:
    col = db[PIPELINE_CANDIDATES]
    todo = await col.find({"pipeline_id": pid, "profile_text": None}).to_list(None)
    total = await col.count_documents({"pipeline_id": pid})
    await step_update(db, pid, "intake", status="running", progress={"done": total - len(todo), "total": total})
    await log_event(db, pid, "intake", f"Reading {len(todo)} profile(s)")
    await asyncio.gather(*(_intake_candidate(db, sem, pid, c) for c in todo))
    parsed = await col.count_documents({"pipeline_id": pid, "profile_text": {"$ne": None}})
    if not parsed:
        raise StepFailureError(
            "intake", "No candidate profile could be read.",
            await _first_error(db, {"pipeline_id": pid, "error": {"$ne": None}}),
        )
    await step_update(db, pid, "intake", status="done", detail=f"{parsed}/{total} parsed")


async def _run_analysis(
    db: AsyncIOMotorDatabase, pid: ObjectId, sem: asyncio.Semaphore, jd_text: str
) -> None:
    col = db[PIPELINE_CANDIDATES]
    todo = await col.find({"pipeline_id": pid, "profile_text": {"$ne": None}, "analysis": None}).to_list(None)
    total = await col.count_documents({"pipeline_id": pid, "profile_text": {"$ne": None}})
    await step_update(db, pid, "analysis", status="running", progress={"done": total - len(todo), "total": total})
    cache: dict[str, dict] = {}
    await asyncio.gather(*(_analyze_candidate(db, sem, jd_text, pid, c, cache) for c in todo))
    ok = await col.count_documents({"pipeline_id": pid, "analysis": {"$ne": None}})
    if not ok:
        err = await _first_error(db, {"pipeline_id": pid, "failed_step": "analysis"})
        raise StepFailureError("analysis", f"All {total} candidate analyses failed.", err)
    await step_update(db, pid, "analysis", status="done", detail=f"{ok}/{total} analyzed")


async def _run_questions(db: AsyncIOMotorDatabase, pipeline: dict) -> None:
    pid = pipeline["_id"]
    counts = pipeline["question_config"]["counts"]
    total_q = sum(counts.values())
    await step_update(db, pid, "questions", status="running", progress={"done": 0, "total": total_q})
    await log_event(db, pid, "questions", f"Generating {total_q} questions {counts}")
    analyses = [
        c["analysis"]
        for c in await db[PIPELINE_CANDIDATES].find({"pipeline_id": pid, "analysis": {"$ne": None}}).to_list(None)
    ]
    questions = await question_agent.generate_questions(pipeline["jd_text"], analyses, counts)
    await db[PIPELINES].update_one({"_id": pid}, {"$set": {"questions": questions}})
    await step_update(
        db, pid, "questions", status="done", progress={"done": len(questions), "total": total_q},
        detail=f"{len(questions)} questions validated",
    )
    await log_event(db, pid, "questions", f"{len(questions)} questions ready")


async def _run_assessment(db: AsyncIOMotorDatabase, pipeline: dict) -> None:
    pid = pipeline["_id"]
    await step_update(db, pid, "assessment", status="running", progress={"done": 0, "total": 1})
    if pipeline.get("assessment_id") and pipeline.get("share_link"):
        await step_update(db, pid, "assessment", status="done", progress={"done": 1, "total": 1}, detail="Already created")
        return
    questions = pipeline.get("questions") or []
    if not questions:
        raise StepFailureError("assessment", "No generated questions available; re-run the questions step.")
    emails = [
        c["email"]
        for c in await db[PIPELINE_CANDIDATES].find({"pipeline_id": pid, "email": {"$ne": None}}).to_list(None)
    ]
    built = await assessment_agent.build_assessment(
        db, workspace_id=str(pipeline["workspace_id"]), user_id=str(pipeline["created_by"]),
        jd_title=pipeline["jd_title"], questions=questions, candidate_emails=emails,
        duration_minutes=pipeline["question_config"]["duration_minutes"],
    )
    await db[PIPELINES].update_one(
        {"_id": pid},
        {"$set": {
            "category_id": ObjectId(built["category_id"]), "assessment_id": ObjectId(built["assessment_id"]),
            "share_id": ObjectId(built["share_id"]), "share_link": built["share_link"],
        }},
    )
    await step_update(
        db, pid, "assessment", status="done", progress={"done": 1, "total": 1},
        detail="Assessment and restricted link created",
    )
    await log_event(db, pid, "assessment", "Assessment + email-restricted share link created")


async def _run_provisioning(db: AsyncIOMotorDatabase, pid: ObjectId) -> dict[str, str]:
    """Create candidate users; returns {candidate_doc_id: temp_password} (kept in memory only)."""
    col = db[PIPELINE_CANDIDATES]
    todo = await col.find(
        {"pipeline_id": pid, "email": {"$ne": None}, "invite_status": {"$ne": "sent"}}
    ).to_list(None)
    await step_update(db, pid, "provisioning", status="running", progress={"done": 0, "total": len(todo)})
    passwords: dict[str, str] = {}
    for cand in todo:
        password = await provision_one(db, cand)
        if password:
            passwords[str(cand["_id"])] = password
            await log_event(db, pid, "provisioning", f"{_label(cand)}: account ready")
        else:
            await log_event(db, pid, "provisioning", f"{_label(cand)}: provisioning failed", "error")
        await step_progress(db, pid, "provisioning")
    failed = len(todo) - len(passwords)
    await step_update(
        db, pid, "provisioning", status="failed" if failed else "done",
        detail=f"{len(passwords)} ready, {failed} failed",
        error={"message": f"{failed} account(s) failed", "detail": await _first_error(db, {"pipeline_id": pid, "failed_step": "provisioning"})} if failed else None,
    )
    return passwords


async def _run_invites(
    db: AsyncIOMotorDatabase, pid: ObjectId, passwords: dict[str, str] | None
) -> dict:
    """Send invites. ``passwords`` None means provisioning did not run in this pass (re-run)."""
    col = db[PIPELINE_CANDIDATES]
    ids: list[ObjectId] | None = None
    if passwords is None:
        todo = await col.count_documents({"pipeline_id": pid, "email": {"$ne": None}, "invite_status": {"$ne": "sent"}})
    else:
        # candidates whose provisioning failed are already marked failed and are not retried here
        ids = [ObjectId(k) for k in passwords]
        todo = len(ids)
    await step_update(db, pid, "invites", status="running", progress={"done": 0, "total": todo})
    await log_event(db, pid, "invites", f"Emailing {todo} candidate(s)")

    async def _progress(ok: bool) -> None:
        if ok:
            await step_progress(db, pid, "invites")

    result = await send_invites(db, pid, candidate_ids=ids, passwords=passwords, on_progress=_progress)
    failed = result["failed"]
    err = None
    if failed:
        err = {
            "message": f"{failed} invite email(s) failed",
            "detail": await _first_error(db, {"pipeline_id": pid, "failed_step": "invites"}),
        }
    await step_update(
        db, pid, "invites", status="failed" if failed else "done",
        detail=f"{result['invited']} invited, {failed} failed", error=err,
    )
    await log_event(db, pid, "invites", f"{result['invited']} invited, {failed} failed", "error" if failed else "info")
    return result


async def _final_status(db: AsyncIOMotorDatabase, pid: ObjectId) -> None:
    attention = await db[PIPELINE_CANDIDATES].count_documents(
        {"pipeline_id": pid, "$or": [
            {"status": {"$in": ["failed", "needs_email"]}}, {"invite_status": "failed"},
        ]}
    )
    status = "done_with_errors" if attention else "done"
    await db[PIPELINES].update_one({"_id": pid}, {"$set": {"status": status, "error": None, "failed_step": None, "updated_at": utcnow()}})
    await log_event(db, pid, "invites", "Pipeline complete. Exam, scoring and the report run automatically after candidates submit.")


async def _fail(db: AsyncIOMotorDatabase, pid: ObjectId, step: str, message: str, detail: str) -> None:
    await step_update(db, pid, step, status="failed", error={"message": message, "detail": detail})
    for k in STEP_KEYS[STEP_KEYS.index(step) + 1:]:
        await step_update(db, pid, k, status="skipped")
    full = f"{message} {detail}".strip()
    await log_event(db, pid, step, full, "error")
    await db[PIPELINES].update_one(
        {"_id": pid},
        {"$set": {"status": "failed", "error": full[:600], "failed_step": step, "updated_at": utcnow()}},
    )


async def run_pipeline(db: AsyncIOMotorDatabase, pipeline_id: str, start_step: str = "intake") -> None:
    """Background entry point. Never raises; failures are stored on the step and pipeline."""
    pid = ObjectId(pipeline_id)
    current = start_step
    start = STEP_KEYS.index(start_step)
    try:
        await db[PIPELINES].update_one({"_id": pid}, {"$set": {"status": "running", "error": None, "failed_step": None}})
        sem = asyncio.Semaphore(max(1, settings.PIPELINE_CONCURRENCY))
        passwords: dict[str, str] | None = None
        for key in STEP_KEYS[start:]:
            current = key
            pipeline = await db[PIPELINES].find_one({"_id": pid})
            if key == "intake":
                await _run_intake(db, pid, sem)
            elif key == "analysis":
                await _run_analysis(db, pid, sem, pipeline["jd_text"])
            elif key == "questions":
                await _run_questions(db, pipeline)
            elif key == "assessment":
                await _run_assessment(db, pipeline)
            elif key == "provisioning":
                passwords = await _run_provisioning(db, pid)
            else:
                await _run_invites(db, pid, passwords)
        await _final_status(db, pid)
    except StepFailureError as exc:
        await _fail(db, pid, exc.step, exc.message, exc.detail)
    except Exception as exc:
        logger.exception(f"AI pipeline {pipeline_id} failed at step {current}")
        detail = getattr(exc, "detail", None) or getattr(exc, "message", None)
        await _fail(db, pid, current, str(detail or exc)[:500] or type(exc).__name__, type(exc).__name__)


def _spawn(db: AsyncIOMotorDatabase, pipeline_id: str, start_step: str = "intake") -> None:
    task = asyncio.create_task(run_pipeline(db, pipeline_id, start_step))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ----------------------------------------------------------------------------- public API
async def start_run(
    db: AsyncIOMotorDatabase, *, pipeline_id: ObjectId, workspace_id: str, jd_title: str,
    jd_text: str, candidates: list[dict], created_by: str,
) -> str:
    """Insert the pipeline + candidate docs, launch the run in the background, return its id."""
    if not candidates:
        raise ValidationException("At least one candidate is required")
    now = utcnow()
    pipeline = {
        "_id": pipeline_id,
        "workspace_id": ObjectId(workspace_id),
        "jd_title": jd_title.strip(),
        "jd_text": jd_text.strip(),
        "status": "queued",
        "question_config": {"counts": dict(QUESTION_COUNTS), "duration_minutes": settings.PIPELINE_EXAM_MINUTES},
        "category_id": None, "assessment_id": None, "share_id": None, "share_link": None,
        "questions": [],
        "error": None, "failed_step": None,
        "steps": _new_steps(len(candidates)),
        "events": [],
        "created_by": ObjectId(created_by),
        "created_at": now,
        "updated_at": now,
    }
    pid = (await db[PIPELINES].insert_one(pipeline)).inserted_id
    # files were saved by the router under the pipeline folder; candidate docs reference the keys
    docs = [_candidate_doc(pid, c, now) for c in candidates]
    await db[PIPELINE_CANDIDATES].insert_many(docs)
    _spawn(db, str(pid))
    return str(pid)


def _public_candidate(c: dict, sub: dict | None) -> dict:
    out = serialize_doc(c)
    out.pop("resume_key", None)
    out.pop("photo_key", None)
    out.pop("input_email", None)
    out["has_resume"] = bool(c.get("resume_key"))
    out["has_photo"] = bool(c.get("photo_key"))
    out["report_status"] = "sent" if c.get("report_sent_at") else ("failed" if c.get("report_error") else "pending")
    out["exam_status"] = getattr(sub.get("status"), "value", sub.get("status")) if sub else None
    out["score"] = sub.get("score") if sub else None
    out["percentage"] = sub.get("percentage") if sub else None
    return out


def _counts(cands: list[dict]) -> dict:
    return {
        "total": len(cands),
        "needs_email": sum(1 for c in cands if c.get("status") == "needs_email"),
        "analyzed": sum(1 for c in cands if c.get("analysis")),
        "invited": sum(1 for c in cands if c.get("invite_status") == "sent"),
        "invite_failed": sum(1 for c in cands if c.get("invite_status") == "failed"),
        "submitted": sum(1 for c in cands if c.get("exam_status") in ("completed", "malpractice", "terminated")),
        "report_sent": sum(1 for c in cands if c.get("report_status") == "sent"),
        "report_failed": sum(1 for c in cands if c.get("report_status") == "failed"),
        "failed": sum(1 for c in cands if c.get("status") == "failed"),
    }


async def _submissions_by_user(db: AsyncIOMotorDatabase, pipeline: dict) -> dict[ObjectId, dict]:
    if not pipeline.get("assessment_id"):
        return {}
    subs = await db.assessment_submissions.find(
        {"assessment_id": pipeline["assessment_id"]},
        {"candidate_id": 1, "status": 1, "score": 1, "percentage": 1},
    ).to_list(None)
    return {s["candidate_id"]: s for s in subs}


def _public_pipeline(pipeline: dict) -> dict:
    out = serialize_doc(pipeline)
    out.pop("questions", None)
    out.pop("jd_text", None)
    out["exam_url"] = exam_url(pipeline["share_link"]) if pipeline.get("share_link") else None
    return out


async def get_run(db: AsyncIOMotorDatabase, pipeline_id: str) -> dict:
    """The run with steps, events, counts and candidates (profile text hidden)."""
    pipeline = await get_pipeline_doc(db, pipeline_id)
    cands = await db[PIPELINE_CANDIDATES].find({"pipeline_id": pipeline["_id"]}, _HIDDEN_CAND).sort("index", 1).to_list(None)
    subs = await _submissions_by_user(db, pipeline)
    public = [_public_candidate(c, subs.get(c.get("user_id"))) for c in cands]
    out = _public_pipeline(pipeline)
    out.setdefault("events", [])
    out["candidates"] = public
    out["counts"] = _counts(public)
    return out


async def get_pipeline_doc(db: AsyncIOMotorDatabase, pipeline_id: str) -> dict:
    try:
        oid = ObjectId(pipeline_id)
    except Exception as exc:
        raise ValidationException("Invalid pipeline id") from exc
    pipeline = await db[PIPELINES].find_one({"_id": oid})
    if not pipeline:
        raise NotFoundException("Pipeline not found")
    return pipeline  # type: ignore[no-any-return]


async def list_runs(
    db: AsyncIOMotorDatabase, *, workspace_ids: list[str] | None, workspace_id: str | None,
    page: int, page_size: int,
) -> dict:
    """Pipelines newest first. ``workspace_ids`` None means all workspaces (super admin)."""
    query: dict = {}
    if workspace_ids is not None:
        query["workspace_id"] = {"$in": [ObjectId(w) for w in workspace_ids if ObjectId.is_valid(w)]}
    if workspace_id:
        query = {"$and": [query, {"workspace_id": ObjectId(workspace_id)}]} if query else {"workspace_id": ObjectId(workspace_id)}
    total = await db[PIPELINES].count_documents(query)
    docs = await db[PIPELINES].find(query, {"jd_text": 0, "questions": 0, "events": 0}).sort("created_at", -1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
    items = []
    for d in docs:
        cands = await db[PIPELINE_CANDIDATES].find({"pipeline_id": d["_id"]}, _HIDDEN_CAND).to_list(None)
        subs = await _submissions_by_user(db, d)
        item = _public_pipeline(d)
        item["counts"] = _counts([_public_candidate(c, subs.get(c.get("user_id"))) for c in cands])
        items.append(item)
    return {"pipelines": items, "pagination": build_pagination_meta(total, page, page_size)}


async def get_questions(db: AsyncIOMotorDatabase, pipeline_id: str) -> dict:
    pipeline = await get_pipeline_doc(db, pipeline_id)
    aid = pipeline.get("assessment_id")
    docs: list[dict] = []
    if aid:
        assessment = await db.assessments.find_one({"_id": aid})
        qids = [q for rd in (assessment or {}).get("rounds", []) for q in rd.get("question_ids", [])]
        docs = await db.questions.find({"_id": {"$in": qids}}).to_list(None)
    questions = [serialize_doc(d) | {"options": d.get("options") or []} for d in docs]
    for q in questions:
        for k in ("category_id", "created_by", "created_at", "updated_at", "image_key"):
            q.pop(k, None)
    counts = {t: sum(1 for q in questions if q["question_type"] == t) for t in QUESTION_COUNTS}
    return {"counts": counts | {"total": len(questions)}, "questions": questions}


async def resend_invites(
    db: AsyncIOMotorDatabase, pipeline_id: str, candidate_id: str | None = None
) -> dict:
    """Resend invites (fresh temp password). One candidate is re-sent even if already sent."""
    pipeline = await get_pipeline_doc(db, pipeline_id)
    if not pipeline.get("share_link"):
        raise ConflictException("The pipeline has no assessment yet (still running or failed)")
    pid = pipeline["_id"]
    ids: list[ObjectId] | None = None
    if candidate_id:
        cand = await get_candidate_doc(db, pipeline_id, candidate_id)
        if not cand.get("email"):
            raise ValidationException("This candidate has no email address")
        ids = [cand["_id"]]
    result = await send_invites(db, pid, candidate_ids=ids, force=bool(candidate_id))
    col = db[PIPELINE_CANDIDATES]
    sent = await col.count_documents({"pipeline_id": pid, "invite_status": "sent"})
    total = await col.count_documents({"pipeline_id": pid, "email": {"$ne": None}})
    failed = await col.count_documents({"pipeline_id": pid, "invite_status": "failed"})
    error = {"message": f"{failed} invite email(s) failed", "detail": await _first_error(db, {"pipeline_id": pid, "invite_status": "failed"})} if failed else None
    await step_update(
        db, pid, "invites", status="failed" if failed else "done",
        progress={"done": sent, "total": total}, detail=f"{sent} invited, {failed} failed", error=error,
    )
    if pipeline.get("status") in ("done", "done_with_errors"):
        await _final_status(db, pid)
    await log_event(db, pid, "invites", f"Resend: {result['invited']} sent, {result['failed']} failed", "error" if result["failed"] else "info")
    return result


async def get_candidate_doc(db: AsyncIOMotorDatabase, pipeline_id: str, candidate_id: str) -> dict:
    pipeline = await get_pipeline_doc(db, pipeline_id)
    try:
        cid = ObjectId(candidate_id)
    except Exception as exc:
        raise ValidationException("Invalid candidate id") from exc
    cand = await db[PIPELINE_CANDIDATES].find_one({"_id": cid, "pipeline_id": pipeline["_id"]})
    if not cand:
        raise NotFoundException("Candidate not found")
    return cand  # type: ignore[no-any-return]


async def retry_step(db: AsyncIOMotorDatabase, pipeline_id: str, step: str | None) -> dict:
    """Re-run the pipeline from ``step`` (default: the failed step), reusing earlier results."""
    pipeline = await get_pipeline_doc(db, pipeline_id)
    if pipeline.get("status") in ("queued", "running"):
        raise ConflictException("The pipeline is already running")
    step = step or pipeline.get("failed_step") or next(
        (s["key"] for s in pipeline["steps"] if s["status"] == "failed"), None
    )
    if step not in STEP_KEYS:
        raise ValidationException(f"Unknown or missing step. Use one of: {', '.join(STEP_KEYS)}")
    idx = STEP_KEYS.index(step)
    if step == "questions":
        if pipeline.get("assessment_id"):
            raise ConflictException("The assessment already exists; questions cannot be regenerated")
        await db[PIPELINES].update_one({"_id": pipeline["_id"]}, {"$set": {"questions": []}})
    for k in STEP_KEYS[idx:]:
        await step_update(db, pipeline["_id"], k, status="pending", detail=None, error=None, started_at=None, finished_at=None)
    await log_event(db, pipeline["_id"], step, f"Re-running from step '{step}'")
    _spawn(db, pipeline_id, step)
    return {"id": pipeline_id, "status": "running", "step": step}


async def recover_interrupted(db: AsyncIOMotorDatabase) -> int:
    """Mark runs that were in flight when the server stopped as failed (retry from the UI)."""
    stuck = await db[PIPELINES].find({"status": {"$in": ["queued", "running"]}}).to_list(None)
    for p in stuck:
        step = next((s["key"] for s in p.get("steps", []) if s["status"] in ("running", "pending")), STEP_KEYS[0])
        await _fail(db, p["_id"], step, "Interrupted by a server restart.", "Use re-run to continue.")
    return len(stuck)
