# SOFTSUAVE-HIRE-BE

FastAPI + Python backend for the SoftSuave Hire interview platform.

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [Project Structure](#project-structure)
3. [Getting Started](#getting-started)
4. [Environment Variables](#environment-variables)
5. [Application Factory](#application-factory)
6. [Database & Indexes](#database--indexes)
7. [Authentication & RBAC](#authentication--rbac)
8. [API Reference](#api-reference)
9. [Component Architecture](#component-architecture)
10. [Common Utilities](#common-utilities)
11. [Error Handling](#error-handling)
12. [Response Format](#response-format)
13. [Question Bank Module](#question-bank-module)
14. [Assessment Module](#assessment-module)
15. [Candidate Module](#candidate-module)
16. [Email Service](#email-service)
17. [Adding a New Module](#adding-a-new-module)
18. [Code Quality & Linting](#code-quality--linting)

---

## Tech Stack

| Package           | Version | Purpose                                  |
| ----------------- | ------- | ---------------------------------------- |
| FastAPI           | 0.115.x | Web framework (async, OpenAPI auto-docs) |
| Uvicorn           | 0.32.x  | ASGI server                              |
| Motor             | 3.x     | Async MongoDB driver                     |
| PyMongo           | 4.x     | MongoDB utilities (used by Motor)        |
| Pydantic v2       | 2.x     | Data validation and settings             |
| pydantic-settings | 2.x     | `.env` file loading via `BaseSettings`   |
| python-jose       | 3.x     | JWT encode/decode (HS256)                |
| passlib[bcrypt]   | 1.x     | Password hashing                         |
| python-multipart  | 0.0.x   | Multipart form data (file uploads)       |
| openpyxl          | 3.x     | Excel file parsing for question import   |
| openai            | 2.x     | AI question generation (OpenAI API)      |
| aiofiles          | 24.x    | Async file I/O                           |
| httpx             | 0.27.x  | Async HTTP (Google OAuth)                |

---

## Project Structure

```
app/
├── main.py                          # Entry point — imports from factory
├── factory.py                       # create_application(): registers middleware,
│                                    #   exception handlers, routers, lifespan
│
├── core/
│   ├── config.py                    # Settings (BaseSettings, reads .env)
│   ├── lifespan.py                  # DB connect on startup, index creation
│   └── dependencies.py              # get_db(request) → AsyncIOMotorDatabase
│
├── common/
│   ├── exceptions.py                # AppException  hierarchy (401/403/404/409/422)
│   ├── exception_handlers.py        # Registers handlers → uniform JSON error shape
│   ├── responses.py                 # success_response() / error_response() helpers
│   ├── utils.py                     # utcnow, generate_uuid, hash_token,
│   │                                #   serialize_doc/docs, paginate_query,
│   │                                #   build_pagination_meta
│   ├── constants/
│   │   └── app_constants.py         # Enums: UserRole, QuestionType, Complexity,
│   │                                #   AssessmentAccessibility, SubmissionStatus,
│   │                                #   SortOrder; ADMIN_ROLES list
│   └── services/
│       └── email_service.py         # send_email() + send_assessment_invite()
│
└── components/                      # Feature modules (each self-contained)
    ├── auth/
    │   ├── auth_schemas.py          # Request/response Pydantic models
    │   ├── auth_service.py          # Business logic (hash, verify, issue tokens)
    │   ├── auth_dependencies.py     # get_current_user, require_admin,
    │   │                            #   require_super_admin, require_candidate
    │   └── auth_router.py           # Route handlers
    ├── workspace/
    │   ├── workspace_schemas.py
    │   ├── workspace_service.py
    │   └── workspace_router.py
    ├── question_bank/
    │   ├── question_schemas.py
    │   ├── question_service.py
    │   └── question_router.py
    ├── assessment/
    │   ├── assessment_schemas.py
    │   ├── assessment_service.py
    │   └── assessment_router.py
    ├── candidate/
    │   ├── candidate_schemas.py
    │   ├── candidate_service.py
    │   └── candidate_router.py
    └── users/
        ├── user_schemas.py
        ├── user_service.py
        └── user_router.py
```

---

## Getting Started

```bash
# 1. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install runtime dependencies
pip install -r requirements.txt

# 3. Install dev dependencies and set up pre-commit hooks
pip install -r requirements-dev.txt
pre-commit install
detect-secrets scan > .secrets.baseline   # first-time only

# 4. Copy and configure environment file
cp .env.example .env

# 5. Start the development server
uvicorn app.main:app --reload --port 8000

# 6. View auto-generated API docs
# Swagger UI: http://localhost:8000/api/docs
# ReDoc:      http://localhost:8000/api/redoc
```

---

## Environment Variables

All settings are loaded by `app/core/config.py` via pydantic-settings. Create `.env` in the project root:

```env
# MongoDB
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=softsuvehire

# JWT
JWT_SECRET_KEY=your_very_long_random_secret_here
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=360
REFRESH_TOKEN_EXPIRE_DAYS=1

# AI (OpenAI)
OPENAI_API_KEY=sk-...

# Google OAuth (Optional)
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# Email (SMTP)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your_app_password

# CORS — JSON array string
CORS_ORIGINS=["http://localhost:5173","https://yourdomain.com"]
```

**Never commit `.env` to version control.**

---

## Application Factory

`app/factory.py` → `create_application()`:

1. Instantiates `FastAPI` with `lifespan` context manager
2. Adds `CORSMiddleware` (reads `settings.CORS_ORIGINS`)
3. Registers exception handlers (AppException , RequestValidationError, generic Exception)
4. Mounts all routers with prefixes:

| Prefix             | Router            | Notes                            |
| ------------------ | ----------------- | -------------------------------- |
| `/api/auth`        | auth_router       | Public + JWT-protected endpoints |
| `/api/users`       | user_router       | Super admin only                 |
| `/api/workspaces`  | workspace_router  | Admin + Super admin              |
| `/api/questions`   | question_router   | Admin + Super admin              |
| `/api/assessments` | assessment_router | Admin + Super admin              |
| `/api/candidate`   | candidate_router  | Candidate JWT + some public      |

---

## Database & Indexes

The Motor client connects on startup via `app/core/lifespan.py` and is stored in `app.state.db`. The `get_db` dependency retrieves it from `request.app.state.db`.

### MongoDB Collections & Indexes

| Collection            | Index                          | Type                             |
| --------------------- | ------------------------------ | -------------------------------- |
| `users`               | `email`                        | Unique                           |
| `refresh_tokens`      | `expires_at`                   | TTL (auto-delete expired tokens) |
| `refresh_tokens`      | `token_hash`                   | Unique                           |
| `workspaces`          | `members.user_id`              | Regular (member lookup)          |
| `question_categories` | `name`                         | Unique                           |
| `questions`           | `category_id + created_at`     | Compound                         |
| `assessments`         | `share_link`                   | Unique                           |
| `assessments`         | `workspace_id + created_at`    | Compound                         |
| `submissions`         | `candidate_id + assessment_id` | Unique (prevents double-entry)   |
| `submissions`         | `assessment_id + status`       | Compound                         |

All indexes are created idempotently in `lifespan.py` at application startup.

---

## Authentication & RBAC

### Token Lifecycle

```
POST /api/auth/admin/login  or  /api/auth/login
  → returns { access_token, refresh_token, user }

access_token:   HS256 JWT, expires in 6 hours
refresh_token:  random 64-byte hex string, SHA-256 hashed before DB storage
                TTL index auto-deletes after 1 day

POST /api/auth/refresh  →  { access_token }  (refresh token rotated)
POST /api/auth/logout   →  deletes refresh_token document from DB
```

### Dependency Guards

`app/components/auth/auth_dependencies.py`:

```python
get_current_user      # verifies Bearer JWT → returns user dict
require_admin         # user.role in ['admin', 'super_admin']
require_super_admin   # user.role == 'super_admin'
require_candidate     # user.role == 'candidate'
```

Usage in a router:

```python
from app.components.auth.auth_dependencies import require_admin, get_current_user

@router.get("/")
async def list_items(
    db = Depends(get_db),
    current_user = Depends(require_admin),
):
    ...
```

### Password Validation (Candidate Registration)

Pydantic `field_validator` on `CandidateRegisterRequest.password` enforces:

- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one digit
- At least one special character

---

## API Reference

### Auth — `/api/auth`

| Method | Path           | Auth                   | Description              |
| ------ | -------------- | ---------------------- | ------------------------ |
| POST   | `/admin/login` | Public                 | Admin login → tokens     |
| POST   | `/login`       | Public                 | Candidate login → tokens |
| POST   | `/register`    | Public                 | Candidate registration   |
| POST   | `/refresh`     | Public (refresh token) | Issue new access token   |
| POST   | `/logout`      | JWT                    | Revoke refresh token     |
| GET    | `/me`          | JWT                    | Get current user profile |

### Users — `/api/users`

| Method | Path   | Auth        | Description                  |
| ------ | ------ | ----------- | ---------------------------- |
| GET    | `/`    | Super Admin | List admin users (paginated) |
| POST   | `/`    | Super Admin | Create admin user            |
| GET    | `/:id` | Super Admin | Get user by ID               |
| PUT    | `/:id` | Super Admin | Update user                  |

### Workspaces — `/api/workspaces`

| Method | Path           | Auth        | Description                       |
| ------ | -------------- | ----------- | --------------------------------- |
| GET    | `/`            | Admin       | List accessible workspaces        |
| POST   | `/`            | Super Admin | Create workspace                  |
| GET    | `/:id`         | Admin       | Get workspace                     |
| PUT    | `/:id`         | Super Admin | Update workspace                  |
| DELETE | `/:id`         | Super Admin | Delete workspace                  |
| POST   | `/:id/invite`  | Super Admin | Invite admin members              |
| GET    | `/:id/members` | Admin       | List members                      |
| GET    | `/admin-users` | Super Admin | List all admin users (for invite) |

### Question Bank — `/api/questions`

| Method | Path              | Auth  | Description                                              |
| ------ | ----------------- | ----- | -------------------------------------------------------- |
| GET    | `/categories`     | Admin | List categories (paginated + search + sort)              |
| POST   | `/categories`     | Admin | Create category                                          |
| PUT    | `/categories/:id` | Admin | Update category                                          |
| DELETE | `/categories/:id` | Admin | Delete category + all its questions                      |
| GET    | `/`               | Admin | List questions (filter by category_id, complexity, type) |
| POST   | `/`               | Admin | Create single question                                   |
| PUT    | `/:id`            | Admin | Update question                                          |
| DELETE | `/:id`            | Admin | Delete question                                          |
| POST   | `/bulk`           | Admin | Bulk create questions (JSON array)                       |
| POST   | `/ai-generate`    | Admin | AI generation via OpenAI (topic + count + type)       |
| POST   | `/excel-columns`  | Admin | Upload `.xlsx` → returns column names                    |
| POST   | `/excel-import`   | Admin | Upload `.xlsx` + column mapping → import questions       |

### Assessments — `/api/assessments`

| Method | Path                        | Auth  | Description                                    |
| ------ | --------------------------- | ----- | ---------------------------------------------- |
| GET    | `/`                         | Admin | List assessments for a workspace               |
| POST   | `/`                         | Admin | Create assessment (with rounds + question_ids) |
| PUT    | `/:id`                      | Admin | Update assessment                              |
| DELETE | `/:id`                      | Admin | Delete assessment                              |
| POST   | `/:id/clone`                | Admin | Clone assessment (new share_link UUID)         |
| GET    | `/:id/submissions`          | Admin | Paginated submissions with candidate lookup    |
| GET    | `/:id/submissions/export`   | Admin | Export submissions as `.xlsx`                  |
| GET    | `/submissions/:id`          | Admin | Single submission detail                       |
| POST   | `/submissions/:id/reaccess` | Admin | Grant candidate re-entry                       |

### Candidate — `/api/candidate`

| Method | Path                           | Auth      | Description                                    |
| ------ | ------------------------------ | --------- | ---------------------------------------------- |
| GET    | `/assessment/:shareLink`       | Public    | Assessment info (no correct answers)           |
| POST   | `/assessment/:shareLink/start` | Candidate | Start → creates/resumes submission             |
| GET    | `/submission/:id/round`        | Candidate | Current round questions (randomized, stripped) |
| POST   | `/submission/:id/answer`       | Candidate | Save answer (dot-notation upsert)              |
| POST   | `/submission/:id/finish-round` | Candidate | Submit round → advance or complete             |
| POST   | `/submission/:id/screenshot`   | Candidate | Upload screenshot (multipart)                  |
| POST   | `/submission/:id/malpractice`  | Candidate | Flag a malpractice event                       |
| GET    | `/live-interviews`             | Admin     | Aggregated active sessions list                |

---

## Component Architecture

Each component (feature module) under `app/components/` follows the same 3-file pattern:

```
<feature>/
├── <feature>_schemas.py    # Pydantic models for request bodies and responses
├── <feature>_service.py    # All business logic (DB queries, transformations)
└── <feature>_router.py     # FastAPI route handlers (thin — delegates to service)
```

**Rule:** Routers only validate input, call the service, and return the response. All logic lives in the service.

---

## Common Utilities

### `app/common/utils.py`

```python
utcnow() → datetime                     # timezone-aware UTC now
generate_uuid() → str                   # UUID4 as string (used as document IDs)
hash_token(token: str) → str            # SHA-256 hex digest (refresh token storage)
generate_secure_token() → str           # 64-byte cryptographically random hex string
serialize_doc(doc: dict) → dict         # converts ObjectId → str, datetime → ISO string
serialize_docs(docs: list) → list       # maps serialize_doc over a list
paginate_query(collection, filter, sort, page, page_size) → (docs, total)
build_pagination_meta(total, page, page_size) → dict
```

### `app/common/responses.py`

```python
success_response(message: str, data: Any) → dict
# → { "success": True, "message": message, "data": data }

error_response(message: str, detail: Any = None) → dict
# → { "success": False, "message": message, "data": None, "detail": detail }
```

Always use these helpers in route handlers instead of returning raw dicts.

---

## Error Handling

`app/common/exceptions.py` defines:

```python
class AppException (Exception):
    status_code: int
    message: str

class UnauthorizedException(AppException )  # 401
class ForbiddenException(AppException )     # 403
class NotFoundException(AppException )      # 404
class ConflictException(AppException )      # 409
class ValidationException(AppException )    # 422
```

`app/common/exception_handlers.py` registers handlers for:

- `AppException ` — returns `error_response()` with the exception's status code
- `RequestValidationError` (Pydantic) — 422 with field-level detail
- `Exception` (catch-all) — 500 with sanitized message

All errors return the same shape:

```json
{
  "success": false,
  "message": "Human-readable message",
  "data": null,
  "detail": "..."
}
```

---

## Response Format

Every successful response:

```json
{
  "success": true,
  "message": "Action description",
  "data": { ... }
}
```

Paginated responses include a `pagination` key inside `data`:

```json
{
  "success": true,
  "message": "...",
  "data": {
    "<resource_key>": [...],
    "pagination": {
      "total": 100,
      "page": 1,
      "page_size": 20,
      "total_pages": 5,
      "has_next": true,
      "has_prev": false
    }
  }
}
```

---

## Question Bank Module

### AI Generation

`question_service.py → ai_generate_questions()`:

1. Calls OpenAI API with a structured prompt requesting JSON output.
2. Parses the JSON response (strips markdown fences if present).
3. Returns a list of question dicts for the frontend to confirm before saving.
4. Confirmed questions are saved via `bulk_create_questions`.

### Excel Import

Two-step flow:

**Step 1 — Column extraction:**

```
POST /api/questions/excel-columns  (multipart: file)
→ { columns: ["Question", "Option A", "Option B", ...] }
```

**Step 2 — Import with mapping:**

```
POST /api/questions/excel-import  (multipart: file + Form: mapping JSON)
mapping = {
  "text_column": "Question",
  "option_columns": ["Option A", "Option B", "Option C", "Option D"],
  "correct_column": "Answer",
  "complexity_column": "Level",
  "default_type": "mcq_single",
  "default_complexity": "medium"
}
```

`process_excel_import()` reads rows with `openpyxl`, maps columns, handles missing values with defaults, and bulk-inserts.

---

## Assessment Module

### Question Randomization

When a candidate starts an assessment:

1. `question_ids` pool per round can be larger than `question_count` — this allows random selection.
2. `random.sample(question_ids_pool, question_count)` picks the subset.
3. MCQ options are shuffled per question.
4. `is_correct` flag is stripped from options before sending to the candidate.

### Score Calculation

`_calculate_score()` in `candidate_service.py`:

- MCQ single: 1 point if selected answer matches correct option text.
- MCQ multiple: 1 point only if all selected answers exactly match all correct options.
- Essay: skipped (manual review required).
- `score_percentage = (correct / total_mcq_questions) * 100`

### Clone

`clone_assessment()` copies all fields from the original and generates a new `share_link` UUID. The clone gets a new `id` and `created_at` but retains all round configs and question references.

---

## Candidate Module

### Submission State Machine

```
pending → in_progress → completed
                    ↘ malpractice
```

- `start_assessment` creates a submission with `status: in_progress`.
- `finish_round`:
  - If `current_round < total_rounds`: increments `current_round`.
  - If last round: calculates score, sets `status: completed`.
- `flag_malpractice`: sets `status: malpractice`, appends to `malpractice_flags[]`. Only fires if `tab_monitoring` is `true` in the assessment's `monitoring_config`.
- `grant_reaccess` (admin): resets `status` to `in_progress`, increments `reaccess_count`.

### Answer Storage

Answers are stored with dot-notation upserts in MongoDB:

```python
# Each answer call does:
db.submissions.update_one(
    {"_id": submission_id},
    {"$set": {f"rounds_data.{round_index}.answers.{question_id}": answer}}
)
```

This makes individual answer saves atomic and avoids overwriting other answers.

### Live Interviews Aggregation

`get_live_interviews()` uses a MongoDB aggregation pipeline:

1. Match `status: in_progress`
2. `$lookup` → `assessments` (join on `assessment_id`)
3. `$lookup` → `users` (join on `candidate_id`)
4. Project required fields + monitoring config
5. Returns paginated results for the admin live-monitoring view

---

## Email Service

`app/common/services/email_service.py`:

```python
await send_email(to: str, subject: str, html_body: str)
await send_assessment_invite(to: str, candidate_name: str, assessment_name: str, share_url: str)
```

Uses SMTP with `STARTTLS`. Configure `SMTP_*` variables in `.env`. The invite email contains a branded HTML template with the assessment link.

---

## Adding a New Module

1. Create folder: `app/components/<module>/`
2. Create `__init__.py` (empty)
3. Create `<module>_schemas.py` — define Pydantic request/response models
4. Create `<module>_service.py` — implement async functions that take `db` as first argument
5. Create `<module>_router.py`:

   ```python
   from fastapi import APIRouter, Depends
   from app.core.dependencies import get_db
   from app.components.auth.auth_dependencies import require_admin

   router = APIRouter(prefix="/<module>", tags=["<Module>"])

   @router.get("/")
   async def list_items(db=Depends(get_db), _=Depends(require_admin)):
       ...
   ```

6. Register the router in `app/factory.py`:
   ```python
   from app.components.<module>.<module>_router import router as <module>_router
   app.include_router(<module>_router, prefix="/api/<module>")
   ```

---

## Scripts

| Command                                           | Description                               |
| ------------------------------------------------- | ----------------------------------------- |
| `uvicorn app.main:app --reload`                   | Start dev server with hot reload          |
| `uvicorn app.main:app --host 0.0.0.0 --port 8000` | Production start                          |
| `pip install -r requirements.txt`                 | Install runtime dependencies              |
| `pip install -r requirements-dev.txt`             | Install dev tools (ruff, pre-commit, etc) |
| `pip freeze > requirements.txt`                   | Update requirements after adding packages |
| `ruff check . --fix`                              | Lint and auto-fix where possible          |
| `ruff format .`                                   | Format all Python files                   |
| `pre-commit run --all-files`                      | Run all hooks against every file manually |

---

## Code Quality & Linting

Tooling is configured in `pyproject.toml`. Pre-commit hooks run automatically on every `git commit`.

### Ruff — Lint

```bash
ruff check .           # check only
ruff check . --fix     # check and auto-fix safe violations
```

Rules enforced: unused imports/variables, bare `except`, boolean comparisons, import order (isort), modern Python syntax (pyupgrade), PEP8 naming, McCabe complexity ≤ 15, basic security (bandit).

### Ruff — Format

```bash
ruff format .          # format all files
ruff format --check .  # check formatting without writing changes (CI)
```

Settings: 100-character line limit · double quotes · 4-space indentation.

### Pre-commit Hooks

Installed hooks run on every `git commit`:

| Hook             | What it catches                                          |
| ---------------- | -------------------------------------------------------- |
| `ruff`           | Lint violations (auto-fixes where possible)              |
| `ruff-format`    | Formatting drift                                         |
| `detect-secrets` | API keys, tokens, high-entropy strings in staged files   |

```bash
pre-commit install              # wire hooks into git (once per clone)
pre-commit run --all-files      # run manually against every file
```

If `detect-secrets` flags a false positive, update the baseline:
```bash
detect-secrets scan > .secrets.baseline
git add .secrets.baseline
```

### Dev Dependencies

All dev tools are in `requirements-dev.txt` (separate from runtime `requirements.txt`):

```
pre-commit, detect-secrets, ruff, pytest, pytest-asyncio
```

---

## Initial Setup — Create First Super Admin

No seeding script is shipped. Create the first super admin via the Python shell:

```python
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.components.auth.auth_service import hash_password
from app.common.utils import utcnow, generate_uuid

async def create_super_admin():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]
    await db.users.insert_one({
        "_id": generate_uuid(),
        "name": "Super Admin",
        "email": "admin@softsuvehire.com",
        "password_hash": hash_password("YourSecurePass@123"),
        "role": "super_admin",
        "created_at": utcnow(),
        "updated_at": utcnow(),
    })
    client.close()

asyncio.run(create_super_admin())
```

After this, use `POST /api/auth/admin/login` to obtain tokens, then use `POST /api/users` to create additional admins via the API.
