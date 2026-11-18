"""MCQ and LLM essay scoring logic."""

import json
import re
from typing import Any, cast

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.logging import logger

_MCQ_TYPES = ("mcq_single", "mcq_multiple", "mcq_multi")
_ESSAY_ATTEMPTS = 3
_SCORING_ERROR = {"is_correct": None, "feedback": "Scoring error"}
Essay = tuple[str, str, str, str]


def _score_mcq(original: dict, given_answer: Any) -> tuple[int, bool]:
    """Return (score, is_correct) for MCQ answer."""
    correct_options = [o for o in original.get("options", []) if o.get("is_correct")]
    correct_ids = {str(o["_id"]) if "_id" in o else o.get("id", "") for o in correct_options}
    correct_texts = {o.get("text", "").strip() for o in correct_options}

    given = [given_answer] if isinstance(given_answer, str) else (given_answer or [])
    given_set = {str(g).strip() for g in given}

    is_correct = bool(given) and (given_set == correct_ids or given_set == correct_texts)
    return (1 if is_correct else 0), is_correct


def _collect_round(
    rd: dict, originals: dict, essays: list[tuple[str, str, str, str]]
) -> tuple[int, int, dict[str, dict]]:
    """Process one round: score MCQs inline and append essays to ``essays``.

    Returns (correct_mcq, total_questions, mcq_results).  The ``essays`` list is
    mutated in place so the caller can batch all rounds' essays into one LLM call.
    """
    answers = rd.get("answers", {})
    correct_mcq = 0
    total = 0
    mcq_results: dict[str, dict] = {}
    for q in rd.get("questions", []):
        qid = q.get("id", "")
        qtype = q.get("type", "essay")
        original = originals.get(qid, {})
        answer = answers.get(qid)
        total += 1
        if qtype in _MCQ_TYPES:
            score, is_correct = _score_mcq(original, answer)
            correct_mcq += score
            mcq_results[qid] = {"is_correct": is_correct}
        else:
            q_text = q.get("text", "") or original.get("question_text", "")
            expected = original.get("correct_answer", "")
            candidate_ans = answer if isinstance(answer, str) else ""
            essays.append((qid, q_text, expected, candidate_ans))
    return correct_mcq, total, mcq_results


def _parse_essay_results(text: str) -> list:
    """Parse the model reply: tolerate ```json fences and a {"results": [...]} wrapper."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group())
    if isinstance(data, dict):
        data = data.get("results") or next((v for v in data.values() if isinstance(v, list)), [])
    return cast(list, data)


def _build_essay_prompt(essays: list[Essay]) -> str:
    numbered = "\n\n".join(
        f"[{i + 1}] Question ID: {qid}\n"
        f"Question: {q_text}\n"
        f"Expected Answer: {expected}\n"
        f"Candidate Answer: {candidate_ans}"
        for i, (qid, q_text, expected, candidate_ans) in enumerate(essays)
    )
    return (
        f"Score the following {len(essays)} essay answer(s).\n"
        "For each, evaluate keyword and content similarity. Mark correct if similarity > 80%. "
        "Nonsensical or empty answers are incorrect. Return one result for EVERY question.\n\n"
        f"{numbered}\n\n"
        "Return ONLY a valid JSON array (one object per question):\n"
        '[{"question_id": "<id>", "is_correct": true/false, "feedback": "<string>"}, ...]'
    )


def _result(r: dict) -> dict:
    return {"is_correct": r.get("is_correct"), "feedback": r.get("feedback", "")}


def _match_results(results: list, expected_ids: list[str]) -> dict[str, dict]:
    """Match results by question id; fall back to position when the model altered the ids."""
    valid = [r for r in results if isinstance(r, dict) and r.get("is_correct") is not None]
    by_id = {str(r.get("question_id")): _result(r) for r in valid}
    if len(results) == len(expected_ids) and not set(by_id) >= set(expected_ids):
        by_id = {
            qid: _result(r)
            for qid, r in zip(expected_ids, results, strict=True)
            if isinstance(r, dict) and r.get("is_correct") is not None
        }
    return {qid: by_id[qid] for qid in expected_ids if qid in by_id}


async def _call_essay_llm(essays: list[Essay]) -> dict[str, dict]:
    """One LLM call for ``essays``; returns qid -> result for the essays it answered."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=60, max_retries=2)
    response = await client.chat.completions.create(
        model=settings.OPENAI_ANALYSIS_MODEL,
        max_tokens=250 * len(essays) + 300,
        messages=[{"role": "user", "content": _build_essay_prompt(essays)}],
    )
    results = _parse_essay_results((response.choices[0].message.content or "").strip())
    return _match_results(results, [qid for qid, *_ in essays])


async def _score_essays_batch(essays: list[Essay]) -> dict[str, dict]:
    """Score all essays with one batched call, re-asking (up to 3 attempts) for any it skipped."""
    if not essays:
        return {}
    if not settings.OPENAI_API_KEY:
        return {qid: {"is_correct": None, "feedback": "Scoring unavailable"} for qid, *_ in essays}
    scored: dict[str, dict] = {}
    pending = list(essays)
    for attempt in range(1, _ESSAY_ATTEMPTS + 1):
        try:
            scored.update(await _call_essay_llm(pending))
        except Exception as exc:
            logger.warning(f"Essay scoring attempt {attempt} failed: {type(exc).__name__}: {exc}")
        pending = [e for e in essays if e[0] not in scored]
        if not pending:
            break
        logger.warning(f"Essay scoring attempt {attempt}: {len(pending)} essay(s) still unevaluated")
    if pending:
        logger.error(f"Essay scoring incomplete: {len(pending)} of {len(essays)} unevaluated")
    return {qid: scored.get(qid, dict(_SCORING_ERROR)) for qid, *_ in essays}


async def score_round(db: AsyncIOMotorDatabase, rd: dict) -> dict:
    """Score a single round. Fetches question originals from DB, scores MCQs inline,
    and batches all essays into one LLM call.

    Returns:
        {
            "score": int,
            "percentage": float,
            "total_questions": int,
            "correctly_answered": int,
            "wrong_answer": int,
            "question_results": {qid: {"is_correct": bool|None, "feedback": str}},
        }
    """
    qids = [ObjectId(q["id"]) for q in rd.get("questions", []) if q.get("id")]
    originals: dict[str, dict] = {}
    if qids:
        docs = await db.questions.find({"_id": {"$in": qids}}).to_list(len(qids))
        originals = {str(d["_id"]): d for d in docs}

    essays: list[tuple[str, str, str, str]] = []
    correct_mcq, total_questions, question_results = _collect_round(rd, originals, essays)

    correct_essay = 0
    if essays:
        essay_results = await _score_essays_batch(essays)
        for qid, res in essay_results.items():
            question_results[qid] = res
            if res.get("is_correct"):
                correct_essay += 1

    total_correct = correct_mcq + correct_essay
    pct = round((total_correct / total_questions * 100) if total_questions > 0 else 0.0, 1)

    return {
        "score": total_correct,
        "percentage": pct,
        "total_questions": total_questions,
        "correctly_answered": total_correct,
        "wrong_answer": total_questions - total_correct,
        "question_results": question_results,
    }


async def calculate_submission_score(db: AsyncIOMotorDatabase, sub: dict) -> dict:
    """Compute overall score across all rounds. All essays are batched in a single LLM call.

    Returns:
        {
            "score": int,
            "percentage": float,
            "total_questions": int,
            "correctly_answered": int,
            "wrong_answer": int,
            "scoring_completed": True,
        }
    """
    all_qids: list[ObjectId] = []
    for rd in sub.get("rounds_data", []):
        for q in rd.get("questions", []):
            if q.get("id"):
                all_qids.append(ObjectId(q["id"]))

    originals: dict[str, dict] = {}
    if all_qids:
        docs = await db.questions.find({"_id": {"$in": all_qids}}).to_list(len(all_qids))
        originals = {str(d["_id"]): d for d in docs}

    total_questions = 0
    correct_mcq = 0
    essays: list[tuple[str, str, str, str]] = []

    for rd in sub.get("rounds_data", []):
        rd_mcq, rd_total, _ = _collect_round(rd, originals, essays)
        correct_mcq += rd_mcq
        total_questions += rd_total

    correct_essay = 0
    if essays:
        essay_results = await _score_essays_batch(essays)
        correct_essay = sum(1 for r in essay_results.values() if r.get("is_correct"))

    total_correct = correct_mcq + correct_essay
    pct = round((total_correct / total_questions * 100) if total_questions > 0 else 0.0, 2)

    return {
        "score": total_correct,
        "percentage": pct,
        "total_questions": total_questions,
        "correctly_answered": total_correct,
        "wrong_answer": total_questions - total_correct,
        "scoring_completed": True,
    }
