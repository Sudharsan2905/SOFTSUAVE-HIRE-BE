from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId
from typing import Optional
from app.common.exceptions import NotFoundException, ConflictException
from app.common.utils import utcnow, serialize_doc, serialize_docs, paginate_query, build_pagination_meta


async def create_category(db: AsyncIOMotorDatabase, data: dict, user_id: str) -> dict:
    if await db.question_categories.find_one({"name": {"$regex": f"^{data['name']}$", "$options": "i"}}):
        raise ConflictException(f"Category '{data['name']}' already exists")

    now = utcnow()
    doc = {
        "name": data["name"],
        "description": data.get("description", ""),
        "created_by": ObjectId(user_id),
        "question_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.question_categories.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_doc(doc)


async def get_categories(
    db: AsyncIOMotorDatabase,
    search: Optional[str],
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
) -> dict:
    skip, limit = paginate_query(page, page_size)
    query = {}
    if search:
        query["name"] = {"$regex": search, "$options": "i"}

    sort_dir = 1 if sort_order == "asc" else -1
    sort_field = sort_by if sort_by in ["name", "created_at", "updated_at", "question_count"] else "created_at"

    total = await db.question_categories.count_documents(query)
    docs = (
        await db.question_categories.find(query)
        .sort(sort_field, sort_dir)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {
        "categories": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }


async def update_category(db: AsyncIOMotorDatabase, category_id: str, data: dict) -> dict:
    if not await db.question_categories.find_one({"_id": ObjectId(category_id)}):
        raise NotFoundException("Category not found")
    update = {k: v for k, v in data.items() if v is not None}
    update["updated_at"] = utcnow()
    await db.question_categories.update_one({"_id": ObjectId(category_id)}, {"$set": update})
    return serialize_doc(await db.question_categories.find_one({"_id": ObjectId(category_id)}))


async def delete_category(db: AsyncIOMotorDatabase, category_id: str):
    if not await db.question_categories.find_one({"_id": ObjectId(category_id)}):
        raise NotFoundException("Category not found")
    await db.question_categories.delete_one({"_id": ObjectId(category_id)})
    await db.questions.delete_many({"category_id": ObjectId(category_id)})


async def get_questions(
    db: AsyncIOMotorDatabase,
    category_id: str,
    search: Optional[str],
    complexity: Optional[str],
    question_type: Optional[str],
    sort_by: str,
    sort_order: str,
    page: int,
    page_size: int,
) -> dict:
    skip, limit = paginate_query(page, page_size)
    query: dict = {"category_id": ObjectId(category_id)}
    if search:
        query["question_text"] = {"$regex": search, "$options": "i"}
    if complexity:
        query["complexity"] = complexity
    if question_type:
        query["question_type"] = question_type

    sort_dir = 1 if sort_order == "asc" else -1
    sort_field = sort_by if sort_by in ["created_at", "updated_at", "complexity"] else "created_at"

    total = await db.questions.count_documents(query)
    docs = (
        await db.questions.find(query)
        .sort(sort_field, sort_dir)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    return {
        "questions": serialize_docs(docs),
        "pagination": build_pagination_meta(total, page, page_size),
    }


async def create_question(
    db: AsyncIOMotorDatabase, category_id: str, data: dict, user_id: str
) -> dict:
    if not await db.question_categories.find_one({"_id": ObjectId(category_id)}):
        raise NotFoundException("Category not found")

    now = utcnow()
    options = data.get("options") or []
    if isinstance(options, list):
        options = [o.model_dump() if hasattr(o, "model_dump") else o for o in options]

    doc = {
        "category_id": ObjectId(category_id),
        "question_text": data["question_text"],
        "question_type": data["question_type"],
        "complexity": data["complexity"],
        "options": options,
        "correct_answer": data.get("correct_answer"),
        "created_by": ObjectId(user_id),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.questions.insert_one(doc)
    doc["_id"] = result.inserted_id
    await db.question_categories.update_one(
        {"_id": ObjectId(category_id)}, {"$inc": {"question_count": 1}}
    )
    return serialize_doc(doc)


async def bulk_create_questions(
    db: AsyncIOMotorDatabase, category_id: str, questions: list, user_id: str
) -> dict:
    if not await db.question_categories.find_one({"_id": ObjectId(category_id)}):
        raise NotFoundException("Category not found")

    now = utcnow()
    docs = []
    for q in questions:
        options = q.get("options") or []
        if isinstance(options, list):
            options = [o.model_dump() if hasattr(o, "model_dump") else o for o in options]
        docs.append(
            {
                "category_id": ObjectId(category_id),
                "question_text": q["question_text"],
                "question_type": q["question_type"],
                "complexity": q["complexity"],
                "options": options,
                "correct_answer": q.get("correct_answer"),
                "created_by": ObjectId(user_id),
                "created_at": now,
                "updated_at": now,
            }
        )

    if docs:
        result = await db.questions.insert_many(docs)
        await db.question_categories.update_one(
            {"_id": ObjectId(category_id)}, {"$inc": {"question_count": len(docs)}}
        )
        return {"created": len(result.inserted_ids)}
    return {"created": 0}


async def update_question(db: AsyncIOMotorDatabase, question_id: str, data: dict) -> dict:
    if not await db.questions.find_one({"_id": ObjectId(question_id)}):
        raise NotFoundException("Question not found")
    update = {k: v for k, v in data.items() if v is not None}
    if "options" in update and isinstance(update["options"], list):
        update["options"] = [
            o.model_dump() if hasattr(o, "model_dump") else o for o in update["options"]
        ]
    update["updated_at"] = utcnow()
    await db.questions.update_one({"_id": ObjectId(question_id)}, {"$set": update})
    return serialize_doc(await db.questions.find_one({"_id": ObjectId(question_id)}))


async def delete_question(db: AsyncIOMotorDatabase, question_id: str):
    q = await db.questions.find_one({"_id": ObjectId(question_id)})
    if not q:
        raise NotFoundException("Question not found")
    await db.questions.delete_one({"_id": ObjectId(question_id)})
    await db.question_categories.update_one(
        {"_id": q["category_id"]}, {"$inc": {"question_count": -1}}
    )


async def ai_generate_questions(
    db: AsyncIOMotorDatabase,
    category_id: str,
    topic: str,
    count: int,
    complexity: str,
    question_type: str,
    user_id: str,
) -> dict:
    try:
        import openai, json, re
        from app.core.config import settings

        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        type_instructions = {
            "mcq_single": "MCQ with exactly one correct answer. Provide 4 options with field 'is_correct': true/false.",
            "mcq_multi": "MCQ with one or more correct answers. Provide 4 options with field 'is_correct': true/false.",
            "essay": "Open-ended essay question. Provide a 'correct_answer' with a model answer.",
        }

        prompt = f"""Generate {count} {complexity}-difficulty technical interview questions on the topic: "{topic}".
Question type: {question_type} - {type_instructions.get(question_type, '')}

Return ONLY a valid JSON array. Each object must have:
- question_text (string) — if the question contains code, wrap it in a markdown fenced code block with the appropriate language tag (e.g. ```python\\n...\\n```)
- options (array of {{id, text, is_correct}}) — only for MCQ types
- correct_answer (string) — only for essay type

No extra explanation outside the JSON array. Return only the JSON array."""

        message = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        content = message.choices[0].message.content
        json_match = re.search(r"\[.*\]", content, re.DOTALL)
        if json_match:
            questions_data = json.loads(json_match.group())
            enriched = [
                {**q, "question_type": question_type, "complexity": complexity}
                for q in questions_data
            ]
            return await bulk_create_questions(db, category_id, enriched, user_id)
    except Exception as e:
        print(f"[AI Generate Error] {e}")

    return {"created": 0, "error": "AI generation failed or unavailable"}


def _find_column(headers_lower: list, name: str):
    """Case-insensitive column lookup; returns original-case header or None."""
    for h in headers_lower:
        if h and h.lower() == name.lower():
            return h
    return None


def _classify_and_build(options_raw, answer_raw):
    """Return (question_type, options_list, correct_answer_text)."""
    options_str = str(options_raw).strip() if options_raw else ""
    answer_str = str(answer_raw).strip() if answer_raw is not None else ""

    if not options_str:
        # No options → essay
        return "essay", [], answer_str or None

    # Split options by comma
    option_texts = [p.strip() for p in options_str.split(",") if p.strip()]

    # Determine correct indices (1-based numbers in answer)
    correct_indices: set[int] = set()
    for part in answer_str.split(","):
        try:
            correct_indices.add(int(part.strip()))
        except ValueError:
            pass

    question_type = "mcq_multi" if len(correct_indices) > 1 else "mcq_single"

    import uuid
    options = [
        {"id": str(uuid.uuid4()), "text": text, "is_correct": (i + 1) in correct_indices}
        for i, text in enumerate(option_texts)
    ]
    return question_type, options, None


async def process_excel_import(
    db: AsyncIOMotorDatabase,
    category_id: str,
    file_data: bytes,
    user_id: str,
) -> dict:
    import openpyxl, io

    wb = openpyxl.load_workbook(io.BytesIO(file_data))
    ws = wb.active
    raw_headers = [cell.value for cell in ws[1]]

    # Locate fixed columns (case-insensitive)
    col_question = _find_column(raw_headers, "question")
    col_options = _find_column(raw_headers, "options")
    col_answer = _find_column(raw_headers, "answer")
    col_complexity = _find_column(raw_headers, "complexity")

    if not col_question:
        return {"created": 0, "error": "Missing 'Question' column"}

    questions = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        row_dict = dict(zip(raw_headers, row))

        question_text = row_dict.get(col_question)
        if not question_text:
            continue

        options_raw = row_dict.get(col_options) if col_options else None
        answer_raw = row_dict.get(col_answer) if col_answer else None
        complexity_raw = str(row_dict.get(col_complexity, "")).lower() if col_complexity else ""
        complexity = complexity_raw if complexity_raw in ("low", "medium", "high") else "medium"

        question_type, options, correct_answer = _classify_and_build(options_raw, answer_raw)

        questions.append({
            "question_text": str(question_text).strip(),
            "question_type": question_type,
            "complexity": complexity,
            "options": options,
            "correct_answer": correct_answer,
        })

    return await bulk_create_questions(db, category_id, questions, user_id)
