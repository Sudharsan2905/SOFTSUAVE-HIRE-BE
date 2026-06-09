"""MCQ and LLM essay scoring logic."""

import json
from typing import Any, cast

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.logging import logger


def _score_mcq(original: dict, given_answer: Any) -> tuple[int, bool]:
    """Return (score, is_correct) for MCQ answer."""
    correct_ids = {
        str(o["_id"]) if "_id" in o else o.get("id", "")
        for o in original.get("options", [])
        if o.get("is_correct")
    }
    given = [given_answer] if isinstance(given_answer, str) else (given_answer or [])
    is_correct = bool(given) and {str(g) for g in given} == correct_ids
    return (1 if is_correct else 0), is_correct


async def _score_essays_batch(
    essays: list[tuple[str, str, str, str]],
) -> dict[str, dict]:
    """Score all essay questions in a single LLM call.

    essays: list of (qid, question_text, expected_answer, candidate_answer)
    Returns dict mapping qid → {is_correct, feedback}
    """
    if not essays:
        return {}
    if not settings.OPENAI_API_KEY:
        return {qid: {"is_correct": None, "feedback": "Scoring unavailable"} for qid, *_ in essays}

    numbered = "\n\n".join(
        f"[{i + 1}] Question ID: {qid}\n"
        f"Question: {q_text}\n"
        f"Expected Answer: {expected}\n"
        f"Candidate Answer: {candidate_ans}"
        for i, (qid, q_text, expected, candidate_ans) in enumerate(essays)
    )
    prompt = (
        f"Score the following {len(essays)} essay answer(s).\n"
        "For each, evaluate keyword and content similarity. Mark correct if similarity > 80%.\n\n"
        f"{numbered}\n\n"
        "Return ONLY a valid JSON array (one object per question):\n"
        '[{"question_id": "<id>", "is_correct": true/false, "feedback": "<string>"}, ...]'
    )

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response.choices[0].message.content or "").strip()
        results: list[dict] = cast(list, json.loads(text))
        return {
            r["question_id"]: {
                "is_correct": r.get("is_correct"),
                "feedback": r.get("feedback", ""),
            }
            for r in results
        }
    except Exception as exc:
        logger.warning("Batch essay scoring failed: %s", exc)
        return {qid: {"is_correct": None, "feedback": "Scoring error"} for qid, *_ in essays}


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

    answers = rd.get("answers", {})
    total_questions = 0
    correct_mcq = 0
    question_results: dict[str, dict] = {}
    essays: list[tuple[str, str, str, str]] = []

    for q in rd.get("questions", []):
        qid = q.get("id", "")
        qtype = q.get("type", "essay")
        original = originals.get(qid, {})
        answer = answers.get(qid)
        total_questions += 1

        if qtype in ("mcq_single", "mcq_multiple", "mcq_multi"):
            score, is_correct = _score_mcq(original, answer)
            correct_mcq += score
            question_results[qid] = {"is_correct": is_correct}
        else:
            q_text = q.get("text", "") or original.get("question_text", "")
            expected = original.get("correct_answer", "")
            candidate_ans = answer if isinstance(answer, str) else ""
            essays.append((qid, q_text, expected, candidate_ans))

    correct_essay = 0
    if essays:
        essay_results = await _score_essays_batch(essays)
        for qid, res in essay_results.items():
            question_results[qid] = res
            if res.get("is_correct"):
                correct_essay += 1

    total_correct = correct_mcq + correct_essay
    pct = round((total_correct / total_questions * 100) if total_questions > 0 else 0.0, 2)

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
        answers = rd.get("answers", {})
        for q in rd.get("questions", []):
            qid = q.get("id", "")
            qtype = q.get("type", "essay")
            original = originals.get(qid, {})
            answer = answers.get(qid)
            total_questions += 1

            if qtype in ("mcq_single", "mcq_multiple", "mcq_multi"):
                score, _ = _score_mcq(original, answer)
                correct_mcq += score
            else:
                q_text = q.get("text", "") or original.get("question_text", "")
                expected = original.get("correct_answer", "")
                candidate_ans = answer if isinstance(answer, str) else ""
                essays.append((qid, q_text, expected, candidate_ans))

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
