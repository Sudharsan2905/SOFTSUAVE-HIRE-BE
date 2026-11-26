"""Generate realistic demo data, write one JSON file per collection, and optionally load it.

Usage (from the SOFTSUAVE-HIRE-BE folder, with the venv active and .env settings loaded):

    python scripts/seed_data.py            # only (re)generate seed_data/<collection>.json
    python scripts/seed_data.py --load     # generate AND upsert into MongoDB
    python scripts/seed_data.py --load --workspace "Softsuave"

Output files are MongoDB Extended JSON (ObjectId -> {"$oid": ...}, dates -> {"$date": ...}),
named after the collection they belong to, so they can also be imported with mongoimport:

    mongoimport --db <db> --collection assessments --jsonArray --file seed_data/assessments.json

Documents get deterministic _ids, so re-running --load upserts instead of duplicating.
The workspace and super admin are looked up in the database (they must already exist).
Seeded candidates all share the password  Candidate@123

The "Campus Hiring Drive 2026" assessment has 50 dedicated candidates: 40 completed (a spread of
scores either side of its 60% pass mark, some with proctoring flags) and 10 in mixed states
(3 in progress, 2 on hold, 3 malpractice, 2 terminated).

Each assessment gets a pass_percentage (40-70). Submission results are NOT stored: the API derives
passed/failed for completed, scored submissions from percentage >= pass_percentage.
"""

import argparse
import hashlib
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bson import ObjectId, json_util
from pymongo import MongoClient, ReplaceOne

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.common.utils import (  # noqa: E402
    encode_permanent_sharelink,
    encode_sharelink,
)
from app.components.auth.auth_service import hash_password  # noqa: E402

OUT_DIR = ROOT / "seed_data"
CANDIDATE_PASSWORD = "Candidate@123"  # pragma: allowlist secret
NOW = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
rng = random.Random(42)


def oid(key: str) -> ObjectId:
    """Deterministic ObjectId from a string key (so re-seeding is idempotent)."""
    return ObjectId(hashlib.md5(f"talentia-seed:{key}".encode()).hexdigest()[:24])  # noqa: S324


def ago(days: float = 0, hours: float = 0, minutes: float = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


# ─── Question bank ────────────────────────────────────────────────────────────

LETTERS = "abcdef"


def mcq(text: str, complexity: str, options: list[str], correct: list[int]) -> dict:
    return {
        "question_text": text,
        "question_type": "mcq_single" if len(correct) == 1 else "mcq_multi",
        "complexity": complexity,
        "options": [
            {"id": LETTERS[i], "text": t, "is_correct": i in correct} for i, t in enumerate(options)
        ],
        "correct_answer": None,
    }


def essay(text: str, complexity: str, model_answer: str) -> dict:
    return {
        "question_text": text,
        "question_type": "essay",
        "complexity": complexity,
        "options": [],
        "correct_answer": model_answer,
    }


CATEGORIES: dict[str, dict] = {
    "Python Programming": {
        "description": "Core Python language, data structures and idioms.",
        "questions": [
            mcq(
                "Which of the following Python data types is immutable?",
                "low",
                ["list", "tuple", "dict", "set"],
                [1],
            ),
            mcq(
                "What does len({1, 2, 2, 3}) return?",
                "low",
                ["2", "3", "4", "It raises an error"],
                [1],
            ),
            mcq(
                "Which keyword turns a normal function into a generator function?",
                "low",
                ["return", "async", "yield", "lambda"],
                [2],
            ),
            mcq(
                "What is the output of print(2 ** 3 ** 2)?",
                "medium",
                ["64", "512", "256", "36"],
                [1],
            ),
            mcq(
                "Which of the following create a shallow copy of a list `a`?",
                "medium",
                ["a[:]", "list(a)", "a.copy()", "b = a"],
                [0, 1, 2],
            ),
            mcq(
                "What is the average-case time complexity of looking up a key in a Python dict?",
                "medium",
                ["O(1)", "O(log n)", "O(n)", "O(n log n)"],
                [0],
            ),
            mcq(
                "What does the @staticmethod decorator do?",
                "medium",
                [
                    "Defines a method that does not receive the instance or class implicitly",
                    "Defines a method that receives the class as its first argument",
                    "Makes the method run only once",
                    "Prevents the method from being overridden",
                ],
                [0],
            ),
            mcq(
                "What does this print?\n\ndef f(x, acc=[]):\n    acc.append(x)\n    return acc\n\nf(1)\nprint(f(2))",
                "high",
                ["[2]", "[1, 2]", "[1]", "None"],
                [1],
            ),
            mcq(
                "Which statement about the GIL (Global Interpreter Lock) in CPython is correct?",
                "high",
                [
                    "It lets multiple threads execute Python bytecode truly in parallel",
                    "It allows only one thread to execute Python bytecode at a time",
                    "It only affects multiprocessing",
                    "It was removed in Python 3.0",
                ],
                [1],
            ),
            essay(
                "Explain the difference between a list and a generator in Python, and describe a situation where a generator is the better choice.",
                "high",
                "A list stores all of its elements in memory and supports indexing and repeated iteration. A generator produces values lazily, one at a time, keeping only its state in memory, and can be consumed once. Generators are preferable for large or unbounded sequences, streaming data or pipelines (for example reading a multi-GB log file line by line) because they use constant memory.",
            ),
        ],
    },
    "JavaScript & React": {
        "description": "Modern JavaScript, browser behaviour and React fundamentals.",
        "questions": [
            mcq(
                "Which React hook is used to run side effects in a function component?",
                "low",
                ["useState", "useEffect", "useRef", "useContext"],
                [1],
            ),
            mcq(
                "Which statement about `let` and `var` is correct?",
                "low",
                [
                    "let is block-scoped, var is function-scoped",
                    "var is block-scoped, let is function-scoped",
                    "Both are block-scoped",
                    "Both are function-scoped",
                ],
                [0],
            ),
            mcq(
                "What is the purpose of the `key` prop when rendering a list in React?",
                "low",
                [
                    "It applies CSS styles to list items",
                    "It helps React identify which items changed, were added or removed",
                    "It encrypts the list data",
                    "It sorts the list automatically",
                ],
                [1],
            ),
            mcq(
                "What does `typeof null` return in JavaScript?",
                "medium",
                ['"null"', '"undefined"', '"object"', '"number"'],
                [2],
            ),
            mcq(
                "What does `0.1 + 0.2 === 0.3` evaluate to?",
                "medium",
                ["true", "false", "undefined", "It throws a TypeError"],
                [1],
            ),
            mcq(
                "Which of the following values are falsy in JavaScript?",
                "medium",
                ["0", '"" (empty string)', "NaN", "[] (empty array)"],
                [0, 1, 2],
            ),
            mcq(
                "Which hook memoizes the result of an expensive computation between renders?",
                "medium",
                ["useCallback", "useMemo", "useReducer", "useLayoutEffect"],
                [1],
            ),
            mcq(
                "What happens when you call [].reduce((a, b) => a + b) with no initial value?",
                "high",
                ["It returns 0", "It returns undefined", "It throws a TypeError", "It returns NaN"],
                [2],
            ),
            essay(
                "Explain how React's virtual DOM improves rendering performance, and mention one case where it does not help.",
                "medium",
                "React keeps a lightweight in-memory tree of the UI. On state changes it builds a new tree, diffs it against the previous one (reconciliation) and applies only the minimal set of real DOM updates, batching them. It does not help when a component re-renders expensively on every change with large lists without keys or memoization, or when the cost is in computation or layout rather than in DOM writes.",
            ),
        ],
    },
    "SQL & Databases": {
        "description": "Relational concepts, SQL querying and database design.",
        "questions": [
            mcq(
                "Which SQL clause filters groups after a GROUP BY aggregation?",
                "low",
                ["WHERE", "HAVING", "ORDER BY", "LIMIT"],
                [1],
            ),
            mcq(
                "Which JOIN returns all rows from the left table and the matching rows from the right table?",
                "low",
                ["INNER JOIN", "LEFT JOIN", "CROSS JOIN", "SELF JOIN"],
                [1],
            ),
            mcq(
                "What is the difference between COUNT(*) and COUNT(column)?",
                "medium",
                [
                    "There is no difference",
                    "COUNT(column) ignores NULL values, COUNT(*) does not",
                    "COUNT(*) ignores NULL values, COUNT(column) does not",
                    "COUNT(column) counts only distinct values",
                ],
                [1],
            ),
            mcq(
                "Which of the following are part of the ACID properties?",
                "medium",
                ["Atomicity", "Isolation", "Durability", "Availability"],
                [0, 1, 2],
            ),
            mcq(
                "Which index structure is the default in most relational databases such as PostgreSQL and MySQL (InnoDB)?",
                "medium",
                ["Hash index", "B-tree", "Bitmap index", "Trie"],
                [1],
            ),
            mcq(
                "Which normal form eliminates transitive dependencies?",
                "medium",
                ["1NF", "2NF", "3NF", "BCNF only"],
                [2],
            ),
            mcq(
                "What does `SELECT NULL = NULL` return in standard SQL?",
                "high",
                ["TRUE", "FALSE", "NULL (unknown)", "An error"],
                [2],
            ),
            essay(
                "Explain what a database index is and the trade-offs of adding many indexes to a write-heavy table.",
                "high",
                "An index is an auxiliary data structure (commonly a B-tree) that lets the database find rows without scanning the whole table, speeding up lookups, joins and sorts. The trade-off is that every INSERT, UPDATE and DELETE must also maintain each index, which slows writes, and indexes consume extra storage. On write-heavy tables you should index only columns that are actually queried.",
            ),
        ],
    },
    "Quantitative Aptitude": {
        "description": "Numerical ability and logical reasoning for graduate hiring.",
        "questions": [
            mcq(
                "A train covers 120 km in 2 hours. At the same speed, how long will it take to cover 300 km?",
                "low",
                ["4 hours", "5 hours", "6 hours", "7.5 hours"],
                [1],
            ),
            mcq(
                "What is the next number in the series 2, 6, 12, 20, 30, ?",
                "low",
                ["40", "42", "44", "36"],
                [1],
            ),
            mcq(
                "5 workers finish a job in 12 days. How many days will 10 workers take, working at the same rate?",
                "low",
                ["3", "6", "10", "24"],
                [1],
            ),
            mcq("What is 15% of 240?", "low", ["30", "32", "36", "40"], [2]),
            mcq(
                "What is the average of 10, 20, 30, 40 and 50?",
                "low",
                ["25", "30", "35", "150"],
                [1],
            ),
            mcq(
                "A shopkeeper buys an item for Rs. 800 and sells it for Rs. 920. What is the profit percentage?",
                "medium",
                ["12%", "14%", "15%", "18%"],
                [2],
            ),
            mcq(
                "A can finish a task in 10 days and B can finish it in 15 days. How many days will they take working together?",
                "medium",
                ["5", "6", "7.5", "12.5"],
                [1],
            ),
            mcq("Find the odd one out: 2, 3, 5, 9, 11, 13", "medium", ["2", "5", "9", "13"], [2]),
        ],
    },
}


def build_questions(workspace_creator: ObjectId) -> tuple[list[dict], list[dict]]:
    categories, questions = [], []
    for ci, (name, cat) in enumerate(CATEGORIES.items()):
        cid = oid(f"cat:{name}")
        created = ago(days=40 - ci)
        categories.append(
            {
                "_id": cid,
                "name": name,
                "description": cat["description"],
                "created_by": workspace_creator,
                "question_count": len(cat["questions"]),
                "created_at": created,
                "updated_at": created,
            }
        )
        for qi, q in enumerate(cat["questions"]):
            questions.append(
                {
                    "_id": oid(f"q:{name}:{qi}"),
                    "category_id": cid,
                    **q,
                    "image_key": None,
                    "created_by": workspace_creator,
                    "created_at": created + timedelta(minutes=qi),
                    "updated_at": created + timedelta(minutes=qi),
                }
            )
    return categories, questions


# ─── Candidates ───────────────────────────────────────────────────────────────

CANDIDATES = [
    ("Aarav", "Sharma", "student", "Anna University", "Chennai, Tamil Nadu"),
    ("Priya", "Nair", "professional", "TCS", "Kochi, Kerala"),
    ("Rohan", "Mehta", "student", "VIT Vellore", "Vellore, Tamil Nadu"),
    ("Ananya", "Iyer", "professional", "Zoho Corporation", "Chennai, Tamil Nadu"),
    ("Karthik", "Subramanian", "professional", "Freshworks", "Chennai, Tamil Nadu"),
    ("Sneha", "Reddy", "student", "NIT Warangal", "Hyderabad, Telangana"),
    ("Vikram", "Singh", "professional", "Infosys", "Bengaluru, Karnataka"),
    ("Divya", "Krishnan", "student", "PSG College of Technology", "Coimbatore, Tamil Nadu"),
    ("Arjun", "Patel", "professional", "Wipro", "Pune, Maharashtra"),
    ("Meera", "Joshi", "student", "BITS Pilani", "Pilani, Rajasthan"),
    ("Siddharth", "Rao", "professional", "Thoughtworks", "Bengaluru, Karnataka"),
    ("Lakshmi", "Venkatesh", "student", "SRM Institute", "Chennai, Tamil Nadu"),
    ("Imran", "Khan", "professional", "HCLTech", "Noida, Uttar Pradesh"),
    ("Nisha", "Thomas", "student", "College of Engineering Trivandrum", "Trivandrum, Kerala"),
]
# Probability each candidate answers a question correctly (their "skill").
SKILL = [0.85, 0.9, 0.55, 0.95, 0.8, 0.7, 0.65, 0.6, 0.75, 0.5, 0.92, 0.45, 0.7, 0.58]


def build_candidates() -> list[dict]:
    pw_hash = hash_password(CANDIDATE_PASSWORD)
    docs = []
    for i, (first, last, ctype, inst, loc) in enumerate(CANDIDATES):
        created = ago(days=35 - i)
        docs.append(
            {
                "_id": oid(f"user:{first}.{last}"),
                "first_name": first,
                "last_name": last,
                "email": f"{first}.{last}@example.com".lower(),
                "password_hash": pw_hash,
                "role": "candidate",
                "is_active": True,
                "email_verified": True,
                "workspace_ids": [],
                "default_workspace_id": None,
                "candidate_type": ctype,
                "google_id": None,
                "phone": f"+91 9{rng.randint(100000000, 999999999)}",
                "dob": f"{rng.randint(1990, 2003)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "gender": "female"
                if first in {"Priya", "Ananya", "Sneha", "Divya", "Meera", "Lakshmi", "Nisha"}
                else "male",
                "institution": inst,
                "location": loc,
                "created_at": created,
                "updated_at": created,
            }
        )
    return docs


# ─── Assessments ──────────────────────────────────────────────────────────────

MON_FULL = {
    "tab_monitoring": True,
    "audio_monitoring": True,
    "video_monitoring": True,
    "screenshot_enabled": True,
    "screenshot_mode": "time_interval",
    "screenshot_interval_seconds": 60,
    "screenshot_count": None,
}
MON_LIGHT = {
    "tab_monitoring": True,
    "audio_monitoring": False,
    "video_monitoring": True,
    "screenshot_enabled": True,
    "screenshot_mode": "count",
    "screenshot_interval_seconds": None,
    "screenshot_count": 5,
}


def pick(
    cat: str, complexities: list[str] | None = None, types: list[str] | None = None, n: int = 99
) -> list[str]:
    """Names of question keys in a category filtered by complexity / type."""
    out = []
    for qi, q in enumerate(CATEGORIES[cat]["questions"]):
        if complexities and q["complexity"] not in complexities:
            continue
        if types and q["question_type"] not in types:
            continue
        out.append(f"q:{cat}:{qi}")
    return out[:n]


MCQ_TYPES = ["mcq_single", "mcq_multi"]
PY, JS, SQL, APT = (
    "Python Programming",
    "JavaScript & React",
    "SQL & Databases",
    "Quantitative Aptitude",
)

ASSESSMENT_SPECS = [
    {
        "key": "python-screening",
        "pass": 50,
        "name": "Python Developer Screening",
        "description": "First-level screening for Python developer applicants: language fundamentals followed by a short aptitude round.",
        "accessibility": "normal",
        "monitoring": None,
        "expected": 40,
        "age_days": 26,
        "rounds": [
            (20, pick(PY, types=MCQ_TYPES, n=8)),
            (15, pick(APT, ["low"], n=5)),
        ],
    },
    {
        "key": "fullstack-js",
        "pass": 60,
        "name": "Full-Stack JavaScript Assessment",
        "description": "Proctored assessment covering core JavaScript semantics and React fundamentals for full-stack roles.",
        "accessibility": "monitoring",
        "monitoring": MON_FULL,
        "expected": 25,
        "age_days": 21,
        "rounds": [(30, pick(JS, types=MCQ_TYPES))],
    },
    {
        "key": "sql-fundamentals",
        "pass": 40,
        "name": "Data & SQL Fundamentals",
        "description": "Tests querying fundamentals, joins, transactions and database design basics.",
        "accessibility": "normal",
        "monitoring": None,
        "expected": 30,
        "age_days": 18,
        "rounds": [(25, pick(SQL, types=MCQ_TYPES))],
    },
    {
        "key": "graduate-aptitude",
        "pass": 50,
        "name": "Graduate Engineer Trainee - Aptitude Test",
        "description": "Campus-drive aptitude test: speed, work, percentage and series reasoning.",
        "accessibility": "normal",
        "monitoring": None,
        "expected": 100,
        "age_days": 14,
        "rounds": [(20, pick(APT))],
    },
    {
        "key": "react-proctored",
        "pass": 70,
        "name": "Frontend Engineer - React Proctored Test",
        "description": "Two-round proctored test on JavaScript fundamentals and React performance hooks.",
        "accessibility": "monitoring",
        "monitoring": MON_LIGHT,
        "expected": 15,
        "age_days": 9,
        "rounds": [
            (20, pick(JS, ["low"], MCQ_TYPES)),
            (20, pick(JS, ["medium", "high"], MCQ_TYPES)),
        ],
    },
    {
        "key": "senior-backend",
        "pass": 65,
        "name": "Senior Backend Engineer - Technical Round",
        "description": "Advanced Python and SQL MCQs followed by written explanations, scored against model answers.",
        "accessibility": "monitoring",
        "monitoring": MON_FULL,
        "expected": 10,
        "age_days": 4,
        "rounds": [
            (25, pick(PY, ["high"], MCQ_TYPES) + pick(SQL, ["high"], ["mcq_single"])),
            (40, pick(PY, types=["essay"]) + pick(SQL, types=["essay"])),
        ],
    },
    {
        "key": "campus-drive-2026",
        "pass": 60,
        "name": "Campus Hiring Drive 2026 - Engineering Screening",
        "description": "Large proctored campus-drive screening in three rounds: quantitative aptitude, Python fundamentals and SQL basics. 50 candidates; 40 finished, the rest are in assorted states.",
        "accessibility": "monitoring",
        "monitoring": MON_FULL,
        "expected": 60,
        "age_days": 12,
        "rounds": [
            (20, pick(APT)),
            (30, pick(PY, types=MCQ_TYPES)),
            (25, pick(SQL, types=MCQ_TYPES)),
        ],
    },
]


def build_assessments(ws_id: ObjectId, creator: ObjectId) -> tuple[list[dict], list[dict]]:
    assessments, shares = [], []
    for spec in ASSESSMENT_SPECS:
        aid = oid(f"assessment:{spec['key']}")
        created = ago(days=spec["age_days"])
        rounds = [
            {
                "round_number": i + 1,
                "question_count": len(qkeys),
                "max_duration_minutes": minutes,
                "question_ids": [oid(k) for k in qkeys],
            }
            for i, (minutes, qkeys) in enumerate(spec["rounds"])
        ]
        assessments.append(
            {
                "_id": aid,
                "workspace_id": ws_id,
                "name": spec["name"],
                "description": spec["description"],
                "rounds": rounds,
                "accessibility": spec["accessibility"],
                "monitoring_config": spec["monitoring"],
                "expected_candidates": spec["expected"],
                "pass_percentage": spec["pass"],
                "created_by": creator,
                "is_active": True,
                "created_at": created,
                "updated_at": created,
                "share_link": encode_permanent_sharelink(str(aid)),
            }
        )
        labels = ["General Link", "Campus Drive"] if spec["expected"] >= 30 else ["General Link"]
        for li, label in enumerate(labels):
            sid = oid(f"share:{spec['key']}:{li}")
            start = created + timedelta(hours=1)
            end = start + timedelta(days=45)
            shares.append(
                {
                    "_id": sid,
                    "assessment_id": aid,
                    "workspace_id": ws_id,
                    "label": label,
                    "monitoring_overrides": None,
                    "share_link": encode_sharelink(
                        str(aid), f"seed{li}", start.isoformat(), end.isoformat()
                    ),
                    "start_time": start,
                    "end_time": end,
                    "restrict_candidate_access": False,
                    "restriction_mode": None,
                    "restricted_emails": [],
                    "is_active": True,
                    "created_by": creator,
                    "created_at": created,
                    "updated_at": created,
                }
            )
    return assessments, shares


# ─── Submissions ──────────────────────────────────────────────────────────────

MALPRACTICE = [
    ("tab_switch", "Candidate switched to another browser tab"),
    ("face_absence", "No face detected in the camera feed for 10 seconds"),
    ("multiple_faces", "More than one face detected in the camera feed"),
    ("fullscreen_exit", "Candidate exited fullscreen mode"),
    ("copy_paste", "Paste action detected in the answer area"),
]

# (assessment key, candidate index, status, extra)
SUBMISSION_PLAN = [
    # Python screening - mostly completed, normal assessment
    *[("python-screening", i, "completed", {}) for i in (0, 1, 2, 3, 4, 6, 8)],
    ("python-screening", 11, "in_progress", {}),
    # Full-stack JS - monitored
    ("fullstack-js", 1, "completed", {}),
    ("fullstack-js", 3, "completed", {}),
    ("fullstack-js", 4, "completed", {"events": 1}),
    ("fullstack-js", 6, "malpractice", {"events": 3}),
    ("fullstack-js", 10, "completed", {}),
    ("fullstack-js", 12, "on_hold", {}),
    # SQL
    *[("sql-fundamentals", i, "completed", {}) for i in (0, 2, 5, 7, 9)],
    ("sql-fundamentals", 13, "terminated", {}),
    # Graduate aptitude (campus drive)
    *[("graduate-aptitude", i, "completed", {}) for i in (0, 2, 5, 7, 9, 11, 13)],
    ("graduate-aptitude", 3, "in_progress", {}),
    # React proctored
    ("react-proctored", 3, "completed", {}),
    ("react-proctored", 10, "completed", {"events": 1}),
    ("react-proctored", 4, "in_progress", {"round": 2}),
    # Senior backend (essay round) - candidates mid-way
    ("senior-backend", 10, "in_progress", {}),
    ("senior-backend", 1, "in_progress", {}),
]


def sanitized(q: dict) -> dict:
    return {
        "id": str(q["_id"]),
        "type": "mcq_multiple" if q["question_type"] == "mcq_multi" else q["question_type"],
        "text": q["question_text"],
        "complexity": q["complexity"],
        "image_key": None,
        "options": [{"id": o["id"], "text": o["text"]} for o in q["options"]],
    }


def answer_for(q: dict, skill: float, rand: random.Random = rng):
    """Pick an answer for a question; correct with probability `skill`."""
    if q["question_type"] == "essay":
        return "A concise written answer covering the main idea and one example."
    correct = [o["id"] for o in q["options"] if o["is_correct"]]
    wrong = [o["id"] for o in q["options"] if not o["is_correct"]]
    if rand.random() < skill:
        chosen = correct
    else:
        chosen = [rand.choice(wrong)]
    return chosen[0] if q["question_type"] == "mcq_single" else chosen


def is_right(q: dict, ans) -> bool:
    if q["question_type"] == "essay":
        return False
    correct = {o["id"] for o in q["options"] if o["is_correct"]}
    given = {ans} if isinstance(ans, str) else set(ans or [])
    return given == correct


def build_submissions(
    assessments,
    shares,
    questions,
    candidates,
    plan=None,
    skills=None,
    rand: random.Random = rng,
):
    plan = SUBMISSION_PLAN if plan is None else plan
    skills = SKILL if skills is None else skills
    qmap = {q["_id"]: q for q in questions}
    amap = {a["_id"]: a for a in assessments}
    first_share = {}
    for s in shares:
        first_share.setdefault(s["assessment_id"], s["_id"])
    cand_ids = [c["_id"] for c in candidates]

    subs = []
    for key, ci, status, extra in plan:
        a = amap[oid(f"assessment:{key}")]
        skill = skills[ci]
        started = a["created_at"] + timedelta(
            days=rand.uniform(1, max(2, (NOW - a["created_at"]).days - 1)),
            hours=rand.uniform(0, 8),
        )
        if status in ("in_progress", "on_hold"):
            started = ago(minutes=rand.randint(4, 25)) if status == "in_progress" else ago(hours=2)
        current = extra.get("round", 1)
        if status in ("completed", "malpractice", "terminated") and "round" not in extra:
            current = len(a["rounds"])

        rounds_data, total_q, correct_q = [], 0, 0
        cursor = started
        for rnd in a["rounds"]:
            qs = [qmap[qid] for qid in rnd["question_ids"]]
            rn = rnd["round_number"]
            done = rn < current or status == "completed"
            partial = rn == current and status in (
                "in_progress",
                "on_hold",
                "malpractice",
                "terminated",
            )
            answers = {}
            if done or partial:
                upto = len(qs) if done else max(1, len(qs) // 2)
                for q in qs[:upto]:
                    answers[str(q["_id"])] = answer_for(q, skill, rand)
            r_correct = sum(
                1 for q in qs if str(q["_id"]) in answers and is_right(q, answers[str(q["_id"])])
            )
            rd = {
                "round_number": rn,
                "question_count": rnd["question_count"],
                "max_duration_minutes": rnd["max_duration_minutes"],
                "questions": [sanitized(q) for q in qs],
                "answers": answers,
                "completed": bool(done),
                "started_at": cursor if (done or partial) else None,
            }
            if done:
                cursor = cursor + timedelta(
                    minutes=rnd["max_duration_minutes"] * rand.uniform(0.55, 0.95)
                )
                rd["completed_at"] = cursor
                rd.update(
                    {
                        "score": r_correct,
                        "percentage": round(r_correct / len(qs) * 100, 1),
                        "total_questions": len(qs),
                        "correctly_answered": r_correct,
                        "wrong_answer": len(qs) - r_correct,
                    }
                )
                total_q += len(qs)
                correct_q += r_correct
            rounds_data.append(rd)

        finished = status in ("completed", "malpractice", "terminated")
        completed_at = cursor if finished else None
        events = []
        for _ in range(extra.get("events", 3 if status == "malpractice" else 0)):
            t, desc = rand.choice(MALPRACTICE)
            events.append(
                {
                    "type": t,
                    "description": desc,
                    "timestamp": started + timedelta(minutes=rand.randint(3, 25)),
                    "round": 1,
                    "is_terminal": False,
                }
            )
        if status == "malpractice" and events:
            events[-1]["is_terminal"] = True

        sub = {
            "_id": oid(f"sub:{key}:{ci}"),
            "assessment_id": a["_id"],
            "candidate_id": cand_ids[ci],
            "share_id": first_share.get(a["_id"]),
            "monitoring_details": a["monitoring_config"] or {},
            "status": status,
            "rounds_data": rounds_data,
            "current_round": current,
            "score": correct_q if status == "completed" else 0,
            "percentage": round(correct_q / total_q * 100, 2)
            if total_q and status == "completed"
            else 0.0,
            "screenshots": [],
            "malpractice_count": len(events),
            "malpractice_events": events,
            "reaccess_count": 1 if status == "on_hold" else 0,
            "is_active": True,
            "started_at": started,
            "completed_at": completed_at,
            "created_at": started,
            "updated_at": completed_at or started,
        }
        if status == "completed":
            sub.update(
                {
                    "total_questions": total_q,
                    "correctly_answered": correct_q,
                    "wrong_answer": total_q - correct_q,
                    "scoring_completed": True,
                    # Marks the session as fully scored, which is what lets the API derive a
                    # pass/fail result from the assessment's pass_percentage.
                    "scoring_status": "scored",
                    "scoring_completed_at": completed_at,
                }
            )
        subs.append(sub)
    return subs


# ─── Campus drive: 50 candidates, 40 completed + 10 in mixed states ───────────

DRIVE_KEY = "campus-drive-2026"
DRIVE_FIRST = [
    ("Aditya", "male"), ("Akash", "male"), ("Bhavna", "female"), ("Chaitanya", "male"),
    ("Deepak", "male"), ("Esha", "female"), ("Farhan", "male"), ("Gayathri", "female"),
    ("Harini", "female"), ("Ishaan", "male"), ("Jyothi", "female"), ("Kavya", "female"),
    ("Manoj", "male"), ("Naveen", "male"), ("Pooja", "female"), ("Rahul", "male"),
    ("Sanjana", "female"), ("Tarun", "male"), ("Uma", "female"), ("Varun", "male"),
    ("Yamini", "female"), ("Zoya", "female"), ("Nikhil", "male"), ("Swetha", "female"),
]  # fmt: skip
DRIVE_LAST = [
    "Agarwal", "Balaji", "Chandran", "Desai", "Gupta", "Hegde", "Menon", "Pillai", "Raghavan",
    "Saxena", "Tiwari", "Verma",
]  # fmt: skip
DRIVE_COLLEGES = [
    ("Anna University", "Chennai, Tamil Nadu"),
    ("VIT Vellore", "Vellore, Tamil Nadu"),
    ("NIT Trichy", "Tiruchirappalli, Tamil Nadu"),
    ("PSG College of Technology", "Coimbatore, Tamil Nadu"),
    ("SRM Institute of Science and Technology", "Chennai, Tamil Nadu"),
    ("Amrita Vishwa Vidyapeetham", "Coimbatore, Tamil Nadu"),
    ("College of Engineering Guindy", "Chennai, Tamil Nadu"),
    ("Kongu Engineering College", "Erode, Tamil Nadu"),
    ("Thiagarajar College of Engineering", "Madurai, Tamil Nadu"),
    ("RV College of Engineering", "Bengaluru, Karnataka"),
]  # fmt: skip

# 40 completed, then 10 in mixed states: (status, extra)
DRIVE_STATES: list[tuple[str, dict]] = [
    *[("completed", {}) for _ in range(40)],
    ("in_progress", {"round": 1}),
    ("in_progress", {"round": 2}),
    ("in_progress", {"round": 3}),
    ("on_hold", {"round": 1}),
    ("on_hold", {"round": 2}),
    ("malpractice", {"events": 3, "round": 1}),
    ("malpractice", {"events": 3, "round": 2}),
    ("malpractice", {"events": 3, "round": 3}),
    ("terminated", {"round": 2}),
    ("terminated", {"round": 3}),
]


def build_drive_candidates(drand: random.Random) -> tuple[list[dict], list[float]]:
    """50 extra candidates (distinct from the original 14) and a skill level for each."""
    taken = {(f, last) for f, last, *_ in CANDIDATES}
    combos = [(f, g, last) for f, g in DRIVE_FIRST for last in DRIVE_LAST if (f, last) not in taken]
    chosen = drand.sample(combos, 50)
    pw_hash = hash_password(CANDIDATE_PASSWORD)
    docs, skills = [], []
    for i, (first, gender, last) in enumerate(chosen):
        inst, loc = drand.choice(DRIVE_COLLEGES)
        created = ago(days=drand.uniform(13, 40))
        docs.append(
            {
                "_id": oid(f"user:{first}.{last}"),
                "first_name": first,
                "last_name": last,
                "email": f"{first}.{last}@example.com".lower(),
                "password_hash": pw_hash,
                "role": "candidate",
                "is_active": True,
                "email_verified": True,
                "workspace_ids": [],
                "default_workspace_id": None,
                "candidate_type": "student" if i % 6 else "professional",
                "google_id": None,
                "phone": f"+91 9{drand.randint(100000000, 999999999)}",
                "dob": f"{drand.randint(1998, 2004)}-{drand.randint(1, 12):02d}-{drand.randint(1, 28):02d}",
                "gender": gender,
                "institution": inst,
                "location": loc,
                "created_at": created,
                "updated_at": created,
            }
        )
        skills.append(round(drand.uniform(0.35, 0.97), 2))
    return docs, skills


def build_drive_plan(drand: random.Random) -> list[tuple[str, int, str, dict]]:
    """Shuffle who ends up in which state, and sprinkle proctoring flags on some finishers."""
    order = list(range(50))
    drand.shuffle(order)
    plan = []
    for ci, (status, extra) in zip(order, DRIVE_STATES, strict=True):
        extra = dict(extra)
        if status == "completed" and drand.random() < 0.22:
            extra["events"] = drand.choice([1, 1, 2])
        plan.append((DRIVE_KEY, ci, status, extra))
    return plan


# ─── Output / load ────────────────────────────────────────────────────────────


def write_json(collection: str, docs: list[dict]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{collection}.json"
    path.write_text(json_util.dumps(docs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}  ({len(docs)} documents)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--load", action="store_true", help="upsert generated data into MongoDB")
    parser.add_argument("--workspace", default="Softsuave", help="workspace name to seed into")
    args = parser.parse_args()

    from app.core.config import settings

    client = MongoClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]
    ws = db.workspaces.find_one({"name": args.workspace})
    admin = db.users.find_one({"role": "super_admin"})
    if not ws or not admin:
        sys.exit(f"Workspace '{args.workspace}' or a super admin was not found in the database.")
    print(f"Seeding into workspace '{ws['name']}' ({ws['_id']})")

    categories, questions = build_questions(admin["_id"])
    candidates = build_candidates()
    assessments, shares = build_assessments(ws["_id"], admin["_id"])
    submissions = build_submissions(assessments, shares, questions, candidates)

    # Campus drive: its own random source, so the data above never changes when this changes.
    drand = random.Random(2026)
    drive_candidates, drive_skills = build_drive_candidates(drand)
    candidates += drive_candidates
    submissions += build_submissions(
        assessments,
        shares,
        questions,
        drive_candidates,
        plan=build_drive_plan(drand),
        skills=drive_skills,
        rand=drand,
    )

    data = {
        "question_categories": categories,
        "questions": questions,
        "users": candidates,
        "assessments": assessments,
        "assessment_shares": shares,
        "assessment_submissions": submissions,
    }
    for name, docs in data.items():
        write_json(name, docs)

    if args.load:
        for name, docs in data.items():
            ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs]
            res = db[name].bulk_write(ops)
            print(f"  loaded {name}: {res.upserted_count} inserted, {res.modified_count} updated")
        print("Done.")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
