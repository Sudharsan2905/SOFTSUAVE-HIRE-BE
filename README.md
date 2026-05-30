# SOFTSUAVE-HIRE-BE

FastAPI + Python backend for the SoftSuave Hire interview platform.

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [Project Structure](#project-structure)
3. [Getting Started](#getting-started)
4. [Environment Variables](#environment-variables)
5. [Docker](#docker)
6. [Application Factory](#application-factory)
7. [Database & Indexes](#database--indexes)
8. [Authentication & RBAC](#authentication--rbac)
9. [Request Validation](#request-validation)
10. [Response Format](#response-format)
11. [Error Handling](#error-handling)
12. [Rate Limiting](#rate-limiting)
13. [API Reference](#api-reference)
14. [Component Architecture](#component-architecture)
15. [Common Utilities](#common-utilities)
16. [Question Bank Module](#question-bank-module)
17. [Assessment Module](#assessment-module)
18. [Candidate Module](#candidate-module)
19. [Email Service](#email-service)
20. [Adding a New Module](#adding-a-new-module)
21. [Code Quality & Linting](#code-quality--linting)

---

## Tech Stack

| Package           | Version   | Purpose                                  |
| ----------------- | --------- | ---------------------------------------- |
| Python            | 3.12      | Runtime (pinned via `.python-version`)   |
| FastAPI           | 0.136.x   | Web framework (async, OpenAPI auto-docs) |
| Uvicorn           | 0.48.x    | ASGI server                              |
| uv                | latest    | Package manager + venv (replaces pip)    |
| Motor             | 3.7.x     | Async MongoDB driver                     |
| PyMongo           | 4.17.x    | MongoDB utilities (used by Motor)        |
| Pydantic v2       | 2.13.x    | Data validation and settings             |
| pydantic-settings | 2.14.x    | `.env` file loading via `BaseSettings`   |
| python-jose       | 3.x       | JWT encode/decode (HS256)                |
| passlib[bcrypt]   | 1.x       | Password hashing                         |
| slowapi           | 0.1.9     | Rate limiting (wraps limits-per-IP)      |
| python-multipart  | 0.0.x     | Multipart form data (file uploads)       |
| openpyxl          | 3.x       | Excel file parsing for question import   |
| openai            | 2.x       | AI question generation (OpenAI API)      |
| aiofiles          | 25.x      | Async file I/O                           |
| aiosmtplib        | 3.x       | Async SMTP email sending                 |
| loguru            | 0.7.x     | Structured logging                       |
| httpx             | 0.28.x    | Async HTTP (Google OAuth)                |

---

## Project Structure

```
.python-version                      # Pins Python 3.12 (read by uv, editors)
requirements/
├── base.txt                         # Production dependencies (pinned)
└── dev.txt                          # Dev tools: ruff, pytest, mypy, etc.
                                     #   (-r base.txt included)
setup/
├── linux/
│   ├── setup.sh                     # Install uv, create venv, install deps,
│   │                                #   copy .env, install pre-commit hooks
│   └── start.sh                     # Activate venv + start uvicorn dev server
├── mac/
│   ├── setup.sh
│   └── start.sh
└── windows/
    ├── setup.bat
    └── start.bat
docker/
├── Dockerfile                       # Multi-stage build (Python 3.12-slim + uv)
└── docker-compose.yml               # API + MongoDB services

app/
├── main.py                          # Entry point — imports app from factory
├── factory.py                       # create_application(): middleware, handlers,
│                                    #   rate limiting, routers, health endpoint
│
├── core/
│   ├── config.py                    # Settings (BaseSettings, reads .env)
│   ├── lifespan.py                  # Startup: _validate_settings(), DB connect,
│   │                                #   screenshots dir creation, index creation
│   ├── limiter.py                   # Module-level slowapi Limiter singleton
│   ├── logging.py                   # Structured logger (loguru) + setup_logging()
│   └── dependencies.py              # get_db(request) → AsyncIOMotorDatabase
│                                    #   DB / CurrentUser / AdminUser / SuperAdminUser
│                                    #   / CandidateUser Annotated type aliases
│
├── common/
│   ├── exceptions.py                # AppException hierarchy (401/403/404/409/422)
│   ├── exception_handlers.py        # Registers handlers → uniform JSON shape
│   ├── responses.py                 # ApiResponse model + success_response() /
│   │                                #   error_response() helpers
│   ├── validators.py                # check_password_strength() — shared across
│   │                                #   all schemas that accept passwords
│   ├── utils.py                     # utcnow, hash_token, serialize_doc/docs,
│   │                                #   paginate_query, build_pagination_meta,
│   │                                #   safe_regex, list_paginated
│   ├── constants/
│   │   └── app_constants.py         # Enums: UserRole, QuestionType, Complexity,
│   │                                #   AssessmentAccessibility, SubmissionStatus,
│   │                                #   SortOrder, MalpracticeType; ADMIN_ROLES list
│   ├── middleware/
│   │   └── logging_middleware.py    # RequestLoggingMiddleware — logs method, path,
│   │                                #   status, duration, X-Request-ID per request
│   └── services/
│       └── email_service.py         # send_email() + send_assessment_invite()
│
├── uploads/
│   └── screenshots/                 # Local screenshot storage (gitignored except .gitkeep)
│                                    #   Files: uploads/screenshots/{submission_id}/round{N}_{ts}.jpg
│
└── components/                      # Feature modules (each self-contained)
    ├── auth/
    ├── workspace/
    ├── question_bank/
    ├── assessment/
    ├── candidate/
    └── users/
```

---

## Getting Started

### Option A — Setup scripts (recommended)

The `setup/` scripts install uv, create a Python 3.12 venv, install all dependencies, copy `.env`, and wire pre-commit hooks in one step.

```bash
# Linux
bash setup/linux/setup.sh
bash setup/linux/start.sh

# macOS
bash setup/mac/setup.sh
bash setup/mac/start.sh

# Windows
setup\windows\setup.bat
setup\windows\start.bat
```

### Option B — Manual setup

```bash
# 1. Install uv (if not already installed)
#    Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
#    Windows (PowerShell)
powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Create virtual environment pinned to Python 3.12
#    uv downloads Python 3.12 automatically if not on your machine
uv venv --python 3.12

# 3. Activate it
source .venv/bin/activate          # Linux / macOS
.venv\Scripts\activate             # Windows

# 4. Install all dependencies (runtime + dev tools)
uv pip install -r requirements/dev.txt

# 5. Copy and configure environment file
cp .env.example .env

# 6. Install pre-commit hooks
pre-commit install
detect-secrets scan --baseline .secrets.baseline   # first-time only

# 7. Start the development server
uvicorn app.main:app --reload --port 8000

# Swagger UI: http://localhost:8000/api/docs
# ReDoc:      http://localhost:8000/api/redoc
```

> **Why uv?** It's 10–100× faster than pip and manages Python versions automatically. It's a drop-in replacement — all `uv pip` commands accept the same flags as `pip`.

---

## Environment Variables

All settings are loaded by `app/core/config.py` via pydantic-settings. Copy `.env.example` to `.env` and fill in values:

```env
# App
APP_NAME=Softsuave Hire
APP_VERSION=1.0.0
APP_DESCRIPTION=Softsuave Hire is a platform to manage the hiring process.

# MongoDB (required)
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=your_database_name

# JWT (required) — generate with: python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET_KEY=your-super-secret-jwt-key-change-in-production
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=360
REFRESH_TOKEN_EXPIRE_DAYS=1

# CORS — JSON array of allowed origins
CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]

# Google OAuth (optional)
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# OpenAI (optional — required for AI question generation)
OPENAI_API_KEY=your-openai-api-key

# SMTP (optional — required for invite emails)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-smtp-user
SMTP_PASSWORD=your-smtp-password

# App behaviour
LOG_LEVEL=INFO
MAX_REACCESS_COUNT=3
SCREENSHOTS_DIR=uploads/screenshots
```

> **Never commit `.env` to version control.** The `.env.example` file serves as the canonical template.

On startup, `_validate_settings()` in `lifespan.py` raises `RuntimeError` immediately if `JWT_SECRET_KEY`, `MONGODB_URL`, or `DATABASE_NAME` is empty, preventing silent misconfiguration.

---

## Docker

Docker files live in the `docker/` folder. The build context is always the project root.

### Services

| Service | Image             | Port  | Description                       |
| ------- | ----------------- | ----- | --------------------------------- |
| `api`   | Built from source | 8000  | FastAPI app (Python 3.12-slim)    |
| `mongo` | mongo:7.0         | 27017 | MongoDB with health check         |

### Dockerfile

Two-stage build:
- **Stage 1 (builder)** — installs `uv`, then uses `uv pip install --system` for fast dependency installation.
- **Stage 2 (runtime)** — `python:3.12-slim`, copies only installed packages, runs as non-root `appuser`.

### Commands

```bash
# Build and start (foreground)
docker compose -f docker/docker-compose.yml up --build

# Start in background
docker compose -f docker/docker-compose.yml up --build -d

# View API logs
docker compose -f docker/docker-compose.yml logs -f api

# Stop
docker compose -f docker/docker-compose.yml down

# Stop and wipe all volumes (DB data + screenshots)
docker compose -f docker/docker-compose.yml down -v
```

> **Note:** `MONGODB_URL` in `.env` is overridden inside Docker to `mongodb://mongo:27017` (the internal service name). Your local `.env` URL is only used for local development.

### Volumes

| Volume       | Mounted at                   | Purpose                         |
| ------------ | ---------------------------- | ------------------------------- |
| `mongo_data` | `/data/db` (mongo container) | Persists MongoDB data           |
| `screenshots`| `/app/uploads/screenshots`   | Persists screenshot files       |

---

## Application Factory

`app/factory.py` → `create_application()`:

1. Instantiates `FastAPI` with the `lifespan` context manager (DB connect + screenshots dir + index creation).
2. Wires the `slowapi` rate limiter (`app.state.limiter`) and registers a custom `_rate_limit_handler` for 429 responses — same `{success, message, data, detail}` shape as all other errors.
3. Adds `RequestLoggingMiddleware` (structured request logs with `X-Request-ID`).
4. Adds `CORSMiddleware` (reads `settings.CORS_ORIGINS`).
5. Registers exception handlers (`AppException`, `RequestValidationError`, generic `Exception`).
6. Mounts all routers:

| Prefix            | Router           | Notes                            |
| ----------------- | ---------------- | -------------------------------- |
| `/api/auth`       | auth_router      | Public + JWT-protected endpoints |
| `/api/users`      | user_router      | Super admin only                 |
| `/api/workspaces` | workspace_router | Admin + Super admin              |
| `/api/questions`  | question_router  | Admin + Super admin              |
| `/api`            | assessment_router| Workspace-scoped assessment paths|
| `/api/candidate`  | candidate_router | Candidate JWT + some public      |

7. Defines the `/api/health` endpoint (pings MongoDB, returns `{status, database}`).

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
| `assessment_submissions` | `candidate_id + assessment_id` | Unique (prevents double-entry) |
| `assessment_submissions` | `assessment_id + status`    | Compound                         |

All indexes are created idempotently at application startup.

---

## Authentication & RBAC

### Token Lifecycle

```
POST /api/auth/admin/login  or  /api/auth/login
  → returns { access_token, refresh_token, token_type, user }

access_token:   HS256 JWT, expires in 6 hours (ACCESS_TOKEN_EXPIRE_MINUTES)
refresh_token:  random 64-byte hex string, SHA-256 hashed before DB storage
                TTL index auto-deletes after 1 day (REFRESH_TOKEN_EXPIRE_DAYS)

POST /api/auth/refresh  →  { access_token }
POST /api/auth/logout   →  deletes refresh_token document from DB
```

### Dependency Guards

`app/components/auth/auth_dependencies.py` exports `Annotated` type aliases for use as FastAPI dependencies:

```python
CurrentUser      # verifies Bearer JWT → returns user dict (any role)
AdminUser        # user.role in ['admin', 'super_admin']
SuperAdminUser   # user.role == 'super_admin'
CandidateUser    # user.role == 'candidate'
```

Usage in a router:

```python
from app.components.auth.auth_dependencies import AdminUser
from app.core.dependencies import DB

@router.get("/")
async def list_items(db: DB, current_user: AdminUser):
    ...
```

### First Super Admin

Use `POST /api/auth/setup` (rate-limited to 3 calls/hour; fails once a super admin already exists):

```json
POST /api/auth/setup
{
  "first_name": "Root",
  "email": "admin@company.com",
  "password": "password" #pragma: allowlist secret
}
```

### Password Strength

`app/common/validators.py` exports `check_password_strength()` — a shared validator applied via `@field_validator` in every schema that accepts a password:

| Schema                   | Endpoint(s)                       |
| ------------------------ | --------------------------------- |
| `SetupRequest`           | `POST /api/auth/setup`            |
| `CandidateRegisterRequest` | `POST /api/auth/register`       |
| `CreateAdminUserRequest` | `POST /api/users`                 |
| `UpdateMeRequest`        | `PATCH /api/users/me`             |

Rules enforced: minimum 8 characters · at least one uppercase · one lowercase · one digit · one special character (`!@#$%^&*(),.?":{}|<>`).

---

## Request Validation

Every POST/PUT/PATCH endpoint uses a typed Pydantic model for the request body — no raw `dict` or `Any` parameters.

### Schema highlights

**`question_schemas.py`** — `CreateQuestionRequest`, `UpdateQuestionRequest`, `BulkQuestionItem` all include a `@model_validator` enforcing MCQ/essay rules:
- `mcq_single` / `mcq_multi`: must have at least one option; at least one `is_correct: true`; `mcq_single` must have exactly one correct option.
- `essay`: must have no options.
- `QuestionOption.id` and `.text` both require `min_length=1`.

**`assessment_schemas.py`** — `MonitoringConfig` validates cross-field screenshot config: if `screenshot_mode` is explicitly set to `"time_interval"`, `screenshot_interval_minutes` is required; if set to `"count"`, `screenshot_count` is required.

**`candidate_schemas.py`**:
- `SubmitAnswerRequest.answer` is typed `str | list[str]` (string for essay, list of option IDs for MCQ).
- `MalpracticeRequest.type` is the `MalpracticeType` enum: `tab_switch`, `multiple_faces`, `no_face`, `background_noise`, `copy_paste`.

**`auth_schemas.py` / `user_schemas.py`** — All password fields use `check_password_strength` from `app/common/validators.py`. `GoogleAuthRequest.credential` and `RefreshTokenRequest.refresh_token` require `min_length=1`.

---

## Response Format

Every endpoint uses `response_model=ApiResponse`. The response shape is identical for both success and error:

```json
{
  "success": true | false,
  "message": "Human-readable description",
  "data": { ... } | null,
  "detail": null | "Error detail string"
}
```

- **Success** — `success: true`, `data` carries the payload, `detail` is `null`.
- **Error** — `success: false`, `data` is `null`, `detail` carries the error reason.

Helpers in `app/common/responses.py`:

```python
success_response(message: str, data: Any = None) -> dict
# → { "success": True, "message": ..., "data": ... }

error_response(message: str, detail: str = "") -> dict
# → { "success": False, "message": ..., "data": None, "detail": ... }
```

### Paginated responses

Paginated `data` includes a `pagination` key:

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
  },
  "detail": null
}
```

---

## Error Handling

`app/common/exceptions.py` defines:

```python
class AppException(Exception):
    status_code: int
    message: str
    detail: str | None

class UnauthorizedException(AppException)  # 401
class ForbiddenException(AppException)     # 403
class NotFoundException(AppException)      # 404
class ConflictException(AppException)      # 409
class ValidationException(AppException)   # 422
```

`app/common/exception_handlers.py` registers three handlers, all calling `error_response()`:

| Handler                  | HTTP status | When                                     |
| ------------------------ | ----------- | ---------------------------------------- |
| `AppException`           | varies      | Domain errors (not found, forbidden, …)  |
| `RequestValidationError` | 422         | Pydantic schema validation failure       |
| `Exception` (catch-all)  | 500         | Unhandled exceptions                     |

The 422 `detail` field contains a semicolon-separated list of field-level error messages, e.g.:
`"body.password: Value error, Password must contain at least one uppercase letter"`

---

## Rate Limiting

`app/core/limiter.py` exports a module-level `slowapi.Limiter` keyed on client IP. The limiter is attached to `app.state.limiter` in the factory. A custom `_rate_limit_handler` returns 429 errors in the standard `{success, message, data, detail}` shape.

Rate limits per auth endpoint:

| Endpoint               | Limit        |
| ---------------------- | ------------ |
| `POST /api/auth/setup` | 3 / hour     |
| `POST /api/auth/admin/login` | 10 / minute |
| `POST /api/auth/login` | 10 / minute  |
| `POST /api/auth/register` | 5 / minute |
| `POST /api/auth/google` | 10 / minute |

All other endpoints are not rate-limited by default.

---

## API Reference

### Health

| Method | Path          | Auth   | Description                         |
| ------ | ------------- | ------ | ----------------------------------- |
| GET    | `/api/health` | Public | Liveness + DB ping (`{status, database}`) |

### Auth — `/api/auth`

| Method | Path           | Auth                   | Rate limit   | Description              |
| ------ | -------------- | ---------------------- | ------------ | ------------------------ |
| POST   | `/setup`       | Public (first-run)     | 3/hour       | Create first super admin |
| POST   | `/admin/login` | Public                 | 10/minute    | Admin login → tokens     |
| POST   | `/login`       | Public                 | 10/minute    | Candidate login → tokens |
| POST   | `/register`    | Public                 | 5/minute     | Candidate registration   |
| POST   | `/google`      | Public                 | 10/minute    | Google OAuth login       |
| POST   | `/refresh`     | Public (refresh token) | —            | Issue new access token   |
| POST   | `/logout`      | JWT                    | —            | Revoke refresh token     |
| GET    | `/me`          | JWT                    | —            | Get current user profile |

### Users — `/api/users`

| Method | Path    | Auth        | Description                  |
| ------ | ------- | ----------- | ---------------------------- |
| PATCH  | `/me`   | JWT         | Update own profile/password  |
| GET    | `/`     | Super Admin | List admin users             |
| POST   | `/`     | Super Admin | Create admin user            |
| GET    | `/:id`  | Super Admin | Get user by ID               |
| PUT    | `/:id`  | Super Admin | Replace user fields          |
| PATCH  | `/:id`  | Super Admin | Partial update user          |

### Workspaces — `/api/workspaces`

| Method | Path              | Auth        | Description                       |
| ------ | ----------------- | ----------- | --------------------------------- |
| GET    | `/`               | Admin       | List accessible workspaces        |
| POST   | `/`               | Super Admin | Create workspace                  |
| GET    | `/admin-users`    | Super Admin | List all admins (for invite UI)   |
| GET    | `/:id`            | Admin       | Get workspace                     |
| PUT    | `/:id`            | Admin       | Update workspace                  |
| DELETE | `/:id`            | Super Admin | Delete workspace                  |
| POST   | `/:id/invite`     | Super Admin | Invite admin members              |
| GET    | `/:id/members`    | Admin       | List workspace members            |

### Question Bank — `/api/questions`

| Method | Path                              | Auth  | Description                              |
| ------ | --------------------------------- | ----- | ---------------------------------------- |
| GET    | `/categories`                     | Admin | List categories (paginated + search)     |
| POST   | `/categories`                     | Admin | Create category                          |
| PUT    | `/categories/:id`                 | Admin | Update category                          |
| DELETE | `/categories/:id`                 | Admin | Delete category + all its questions      |
| GET    | `/categories/:id/questions`       | Admin | List questions (filter/sort/paginate)    |
| POST   | `/categories/:id/questions`       | Admin | Create single question                   |
| POST   | `/categories/:id/bulk`            | Admin | Bulk create questions (JSON array)       |
| POST   | `/categories/:id/ai-generate`     | Admin | AI generation (topic + count + type)     |
| POST   | `/categories/:id/excel-import`    | Admin | Upload `.xlsx` + column map → import     |
| PUT    | `/:id`                            | Admin | Update question                          |
| DELETE | `/:id`                            | Admin | Delete question                          |

### Assessments — `/api/workspaces/:workspace_id/assessments`

| Method | Path                                    | Auth  | Description                         |
| ------ | --------------------------------------- | ----- | ----------------------------------- |
| GET    | `/`                                     | Admin | List assessments (paginated)        |
| POST   | `/`                                     | Admin | Create assessment with rounds       |
| GET    | `/:id`                                  | Admin | Get assessment                      |
| PUT    | `/:id`                                  | Admin | Update assessment                   |
| GET    | `/:id/submissions`                      | Admin | Paginated submissions + candidate   |
| GET    | `/:id/submissions/:sub_id`              | Admin | Single submission detail            |
| POST   | `/:id/submissions/:sub_id/reaccess`     | Admin | Grant candidate re-entry            |
| GET    | `/:id/export`                           | Admin | All submissions for Excel export    |

Also: `GET /api/assessments/share/:share_link` — public, returns assessment metadata (no correct answers).

### Candidate — `/api/candidate`

| Method | Path                            | Auth      | Description                                    |
| ------ | ------------------------------- | --------- | ---------------------------------------------- |
| GET    | `/assessment/:shareLink`        | Public    | Assessment info (no correct answers)           |
| POST   | `/assessment/:shareLink/start`  | Candidate | Start or resume submission                     |
| GET    | `/submission/:id/round`         | Candidate | Current round questions (randomised, stripped) |
| POST   | `/submission/:id/answer`        | Candidate | Save answer (`str` or `list[str]`)             |
| POST   | `/submission/:id/finish-round`  | Candidate | Advance to next round or complete              |
| POST   | `/submission/:id/screenshot`    | Candidate | Upload screenshot (JPEG/PNG, max 2 MB)         |
| POST   | `/submission/:id/malpractice`   | Candidate | Flag a malpractice event (enum-validated type) |
| GET    | `/live-interviews`              | Admin     | Paginated in-progress sessions                 |

---

## Component Architecture

Each component under `app/components/` follows the same 3-file pattern:

```
<feature>/
├── <feature>_schemas.py    # Pydantic request models with field + cross-field validators
├── <feature>_service.py    # All business logic (DB queries, transformations)
└── <feature>_router.py     # Thin handlers: validate → call service → return success_response()
```

**Rule:** Routers only call the service and return `success_response(...)`. All logic lives in the service.

---

## Common Utilities

### `app/common/utils.py`

```python
utcnow() → datetime                    # timezone-aware UTC now
hash_token(token: str) → str           # SHA-256 hex digest (refresh token storage)
generate_secure_token() → str          # 64-byte cryptographically random hex string
generate_sharelink(workspace_id) → str # URL-safe UUID for assessment share links
safe_regex(term: str) → str            # re.escape() wrapper for MongoDB $regex queries
serialize_doc(doc: dict) → dict        # ObjectId → str, datetime → ISO string
serialize_docs(docs: list) → list      # maps serialize_doc over a list
paginate_query(page, page_size) → (skip, limit)
list_paginated(collection, query, sort_field, sort_dir, skip, limit, allowed_sort_fields)
build_pagination_meta(total, page, page_size) → dict
```

### `app/common/validators.py`

```python
check_password_strength(v: str) → str
# Raises ValueError if password lacks uppercase, lowercase, digit, or special char.
# Used as @field_validator in SetupRequest, CandidateRegisterRequest,
# CreateAdminUserRequest, and UpdateMeRequest.
```

### `app/common/responses.py`

```python
class ApiResponse(BaseModel):
    success: bool
    message: str
    data: Any = None
    detail: str | None = None

success_response(message: str, data: Any = None) → dict
error_response(message: str, detail: str = "") → dict
```

---

## Question Bank Module

### AI Generation

`question_service.py → ai_generate_questions()`:

1. Calls OpenAI API with a structured prompt requesting a JSON array of questions.
2. Parses the response (strips markdown fences if present).
3. Returns a list of question dicts for the frontend to confirm before saving.
4. Confirmed questions are saved via `bulk_create_questions`.

### Excel Import

```
POST /api/questions/categories/:id/excel-import
  multipart: file (.xlsx) + Form: column_map (JSON string)

column_map example:
{
  "question_column": "Question",
  "answer_column": "Answer",
  "complexity_column": "Level"
}
```

`process_excel_import()` reads rows with `openpyxl`, maps columns, handles missing values with defaults, and bulk-inserts. An invalid `column_map` JSON falls back silently to an empty dict.

### MCQ Validation

`CreateQuestionRequest` and `BulkQuestionItem` reject invalid MCQ/essay payloads at the Pydantic layer before reaching the service:

- MCQ types: at least one option, at least one `is_correct: true`, `mcq_single` requires exactly one correct option.
- Essay: no options allowed.

---

## Assessment Module

### Question Randomization

When a candidate starts an assessment, `_build_round_data()` and `_sanitize_question()` in `candidate_service.py` handle sampling:

1. `question_ids` pool per round can exceed `question_count` — enables random selection.
2. `_rng.sample(pool, question_count)` picks the subset (`_rng = random.SystemRandom()` — OS-backed entropy).
3. MCQ options are shuffled per question; `is_correct` flag and `correct_answer` are stripped.
4. Questions within each round are shuffled before delivery.

### Score Calculation

`_calculate_score()` delegates per-question scoring to `_score_question()`:

- MCQ single: 1 point if selected option ID matches the correct one.
- MCQ multi: 1 point only if all selected IDs exactly match all correct IDs.
- Essay: skipped (manual review required).
- `score_percentage = (correct / total_mcq_questions) * 100`

### Monitoring Config

`MonitoringConfig` cross-field validation:

- `screenshot_mode = "time_interval"` (explicit) → `screenshot_interval_minutes` required.
- `screenshot_mode = "count"` (explicit) → `screenshot_count` required.
- If `screenshot_mode` is not sent, defaults apply without validation.

---

## Candidate Module

### Submission State Machine

```
pending → in_progress → completed
                    ↘ malpractice
```

- `start_assessment` — creates `in_progress` submission (or resumes existing). Delegates existing-submission handling to `_handle_existing_submission()`.
- `finish_round` — advances `current_round` or sets `status: completed` on last round.
- `flag_malpractice` — sets `status: malpractice`. Silently skips if `tab_monitoring` is `false`. `type` must be one of: `tab_switch`, `multiple_faces`, `no_face`, `background_noise`, `copy_paste`.
- `grant_reaccess` (admin) — resets to `pending`, increments `reaccess_count`. Capped at `settings.MAX_REACCESS_COUNT`.

### Answer Storage

Answers are stored with dot-notation MongoDB upserts:

```python
db.assessment_submissions.update_one(
    {"_id": submission_id},
    {"$set": {f"rounds_data.{round_index}.answers.{question_id}": answer}}
)
```

`answer` is `str` (essay / mcq_single) or `list[str]` (mcq_multi).

### Screenshot Storage

`POST /submission/:id/screenshot` validates before processing:
- Content-Type must be `image/jpeg` or `image/png` — returns 422 otherwise.
- File size must not exceed 2 MB — returns 422 otherwise.

Files are saved to the local filesystem via `save_screenshot()`:

```
uploads/screenshots/{submission_id}/round{N}_{YYYYMMDD_HHmmss_ffffff}.jpg
```

The file path (not the raw bytes) is stored in the submission document:

```json
{
  "path": "uploads/screenshots/abc123/round1_20240101_120000_000000.jpg",
  "round": 1,
  "taken_at": "2024-01-01T12:00:00Z"
}
```

The `SCREENSHOTS_DIR` setting controls the storage root (default: `uploads/screenshots`). In Docker, it is overridden to `/app/uploads/screenshots` which is backed by a named volume.

---

## Email Service

`app/common/services/email_service.py`:

```python
await send_email(to: str, subject: str, html_body: str)
await send_assessment_invite(to: str, candidate_name: str, assessment_name: str, share_url: str)
```

Uses async SMTP (`aiosmtplib`) with `STARTTLS`. Configure `SMTP_*` variables in `.env`. The invite email contains a branded HTML template with the assessment share link.

---

## Adding a New Module

1. Create `app/components/<module>/` with `__init__.py`.
2. Create `<module>_schemas.py` — Pydantic models with field validators.
3. Create `<module>_service.py` — `async def` functions taking `db: AsyncIOMotorDatabase` as first arg.
4. Create `<module>_router.py`:

   ```python
   from fastapi import APIRouter
   from app.common.responses import ApiResponse, success_response
   from app.components.auth.auth_dependencies import AdminUser
   from app.core.dependencies import DB

   router = APIRouter()

   @router.get("/", response_model=ApiResponse)
   async def list_items(db: DB, current_user: AdminUser):
       result = await <module>_service.get_items(db)
       return success_response("Items retrieved", result)
   ```

5. Register in `app/factory.py`:
   ```python
   from app.components.<module>.<module>_router import router as <module>_router
   app.include_router(<module>_router, prefix="/api/<module>", tags=["<Module>"])
   ```

---

## Code Quality & Linting

All tooling is configured in `pyproject.toml`. Pre-commit hooks run automatically on every `git commit`.

### Pre-commit hooks

| Hook                   | What it checks                                              |
| ---------------------- | ----------------------------------------------------------- |
| `ruff`                 | Lint violations (auto-fixes where safe)                     |
| `ruff-format`          | Formatting drift (100-char limit, double quotes)            |
| `trailing-whitespace`  | Trailing spaces on any line                                 |
| `end-of-file-fixer`    | Missing newline at end of file                              |
| `check-yaml`           | YAML syntax (docker-compose, pre-commit config)             |
| `check-toml`           | TOML syntax (pyproject.toml)                                |
| `check-added-large-files` | Blocks files over 1 MB                                   |
| `check-merge-conflict` | Blocks unresolved `<<<<<<< HEAD` markers                    |
| `debug-statements`     | Blocks `pdb` / `breakpoint()` left in code                  |
| `detect-secrets`       | API keys, tokens, high-entropy strings in staged files      |
| `bandit`               | Security anti-patterns (skips B101 asserts, B104 binding)   |
| `mypy`                 | Static type checking on `app/` only (strict untyped defs)   |

```bash
pre-commit install              # wire hooks into git (once per clone)
pre-commit run --all-files      # run manually against every file
```

`pip-audit` (CVE scan) is available as a manual stage:
```bash
pre-commit run pip-audit --hook-stage manual
```

### Ruff

```bash
ruff check .           # lint only
ruff check . --fix     # lint + auto-fix safe violations
ruff format .          # format all files
ruff format --check .  # CI check (no writes)
```

Rules: unused imports/variables, bare `except`, boolean comparisons, import order, modern syntax, PEP8 naming, McCabe complexity ≤ 15, bandit security subset (`S` rules).

### mypy

```bash
mypy app/
```

Configured in `mypy.ini`: `python_version = 3.12`, `disallow_untyped_defs = true`, `warn_return_any = true`, `warn_unused_ignores = true`. Test files are excluded. The pre-commit hook runs mypy in an isolated environment with `motor`, `pymongo`, and `slowapi` stubs pinned.

### bandit

```bash
bandit -c pyproject.toml -r app/
```

Skips: `B101` (assert statements in tests), `B104` (binding to all interfaces in dev).

### pytest

```bash
pytest                               # run all tests (parallel via pytest-xdist)
pytest --cov=app --cov-report=html   # with coverage report
```

Tests run in parallel with `addopts = "-n auto"` (pytest-xdist). All tests use `mongomock_motor` — no real MongoDB required.

### Commands reference

| Command                                                          | Description                               |
| ---------------------------------------------------------------- | ----------------------------------------- |
| `uvicorn app.main:app --reload`                                  | Start dev server with hot reload          |
| `uv pip install -r requirements/dev.txt`                         | Install all dependencies (runtime + dev)  |
| `uv pip install -r requirements/base.txt`                        | Install runtime dependencies only         |
| `uv pip install <package>`                                       | Install a single package                  |
| `ruff check . --fix && ruff format .`                            | Lint + format (run before committing)     |
| `mypy app/`                                                      | Type-check app source                     |
| `pytest`                                                         | Run full test suite                       |
| `pytest --cov=app --cov-report=html`                             | Run tests with HTML coverage report       |
| `pre-commit run --all-files`                                     | Run all hooks against every file          |
| `pre-commit run pip-audit --hook-stage manual`                   | Scan dependencies for known CVEs          |
| `detect-secrets scan --baseline .secrets.baseline`               | Regenerate secrets baseline               |
| `docker compose -f docker/docker-compose.yml up --build`         | Build and start Docker services           |
| `docker compose -f docker/docker-compose.yml up --build -d`      | Start Docker services in background       |
| `docker compose -f docker/docker-compose.yml logs -f api`        | Tail API logs                             |
| `docker compose -f docker/docker-compose.yml down`               | Stop Docker services                      |
| `docker compose -f docker/docker-compose.yml down -v`            | Stop and wipe all volumes                 |
