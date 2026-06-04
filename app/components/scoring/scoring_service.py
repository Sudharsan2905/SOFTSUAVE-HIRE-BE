"""MCQ and LLM essay scoring logic."""

import asyncio
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


async def score_essay(question_text: str, expected_answer: str, candidate_answer: str) -> dict:
    """Score essay answer using Claude/Anthropic API."""
    if not candidate_answer or not candidate_answer.strip():
        return {"is_correct": False, "similarity_score": 0.0, "feedback": "No answer provided"}

    if not settings.OPENAI_API_KEY:
        return {"is_correct": None, "similarity_score": None, "feedback": "LLM scoring unavailable"}

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.OPENAI_API_KEY)
        prompt = (
            f"Question: {question_text}\n"
            f"Expected Answer: {expected_answer}\n"
            f"Candidate Answer: {candidate_answer}\n\n"
            "Evaluate keyword and content similarity. If similarity > 80%, mark correct.\n"
            'Return ONLY valid JSON: {"is_correct": bool, '
            '"similarity_score": float, "feedback": "string"}'
        )
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        return cast(dict[str, Any], json.loads(text))
    except Exception as exc:
        logger.warning("Essay scoring failed: %s", exc)
        return {"is_correct": None, "similarity_score": None, "feedback": "Scoring error"}


async def calculate_submission_score(db: AsyncIOMotorDatabase, sub: dict) -> dict:
    """Compute per-round and overall scores. Returns score summary dict."""
    all_qids: list[ObjectId] = []
    for rd in sub.get("rounds_data", []):
        for q in rd.get("questions", []):
            if q.get("id"):
                all_qids.append(ObjectId(q["id"]))

    originals: dict[str, dict] = {}
    if all_qids:
        docs = await db.questions.find({"_id": {"$in": all_qids}}).to_list(len(all_qids))
        originals = {str(d["_id"]): d for d in docs}

    per_round: list[dict] = []
    correct_mcq = 0

    for rd in sub.get("rounds_data", []):
        answers = rd.get("answers", {})
        round_correct = 0
        round_total = 0
        round_essays: list[tuple[str, str, str, str]] = []

        for q in rd.get("questions", []):
            qid = q.get("id", "")
            qtype = q.get("type", "essay")
            original = originals.get(qid, {})
            answer = answers.get(qid)

            if qtype in ("mcq_single", "mcq_multiple", "mcq_multi"):
                score, _ = _score_mcq(original, answer)
                round_correct += score
                round_total += 1
                correct_mcq += score
            elif qtype == "essay":
                q_text = q.get("text", "") or original.get("question_text", "")
                expected = original.get("correct_answer", "")
                candidate_ans = answer if isinstance(answer, str) else ""
                round_essays.append((qid, q_text, expected, candidate_ans))

        essay_results: dict[str, dict[str, Any]] = {}
        if round_essays:
            tasks = [score_essay(q_text, exp, cand) for _, q_text, exp, cand in round_essays]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (qid, _, _, _), res in zip(round_essays, results, strict=False):
                if isinstance(res, Exception):
                    essay_results[qid] = {"is_correct": None, "similarity_score": None}
                else:
                    result_dict = cast(dict[str, Any], res)
                    essay_results[qid] = result_dict
                    if result_dict.get("is_correct"):
                        round_correct += 1
                        round_total += 1

        pct = round((round_correct / round_total * 100) if round_total > 0 else 0.0, 2)
        per_round.append(
            {
                "round_number": rd.get("round_number", 1),
                "score": round_correct,
                "percentage": pct,
                "essay_scores": essay_results,
            }
        )

    total_correct = sum(r["score"] for r in per_round)
    total_questions = sum(len(rd.get("questions", [])) for rd in sub.get("rounds_data", []))
    overall_pct = round((total_correct / total_questions * 100) if total_questions > 0 else 0.0, 2)

    return {
        "score": correct_mcq,
        "percentage": overall_pct,
        "per_round_scores": per_round,
        "scoring_completed": True,
    }
