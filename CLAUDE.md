# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Talentia backend: FastAPI (async) on Python 3.12 with MongoDB through Motor. It serves the React frontend in the sibling `SOFTSUAVE-HIRE-FE` repo (a workspace-level `CLAUDE.md` in the parent folder, if present locally, covers cross-repo contracts). `README.md` is detailed but partly stale. It describes a smaller module set than exists and a 3-file component pattern, while the code also has repositories, response models and more components. Trust the code.

## Commands

Windows scripts live in `setup/windows/` (`setup.bat` once, then `start.bat`). Linux and mac equivalents are in `setup/linux` and `setup/mac`. Manual equivalent:

```bash
uv venv --python 3.12 && .venv\Scripts\activate
uv pip install -r requirements/dev.txt
cp .env.example .env                      # MONGODB_URL, DATABASE_NAME, JWT_SECRET_KEY are required
uvicorn app.main:app --reload --port 8000 # Swagger: /api/docs
```

```bash
pytest                                    # all tests; addopts is "-n auto" (xdist parallel)
pytest test/test_auth_service.py          # single file
pytest test/test_auth_service.py -k name -n0   # single test, no parallelism
pytest --cov=app                          # coverage (Sonar config in sonar-project.properties)
ruff check . --fix && ruff format .       # lint + format (line length 100)
mypy --config-file=mypy.ini app           # disallow_untyped_defs is on, so all functions need annotations
pre-commit run --all-files                # ruff, mypy, bandit, detect-secrets, file hygiene
docker compose -f docker/docker-compose.yml up --build   # API + Mongo (overrides MONGODB_URL)
```

Pushes to `main` auto-deploy over SSH (`.github/workflows/deploy.yml`): `git pull`, `pip install -r requirements/base.txt`, then restart the `softsuave-api` systemd unit. Runtime dependencies must therefore be in `requirements/base.txt`, and dev-only tools in `requirements/dev.txt`.

## Architecture

- **App wiring:** `app/main.py` imports `create_application()` from `app/factory.py`. The factory sets up the lifespan (`app/core/lifespan.py`: validates settings, connects Motor into `app.state.db`, creates indexes idempotently), slowapi rate limiting, request-logging middleware (`X-Request-ID`), CORS (`CORS_ORIGINS` and `CORS_ORIGIN_REGEX`), exception handlers, and all routers under `/api`. Settings come from `app/core/config.py` (pydantic-settings).
- **Feature modules** in `app/components/<name>/`: `auth`, `users`, `workspace`, `question_bank`, `assessment`, `candidate`, `notifications`, `proctoring`, `scoring`, `livekit`, `storage` (S3), `version_history`, `websocket`. The pattern is `*_schemas.py` (Pydantic request models and validators), `*_service.py` (all business logic and DB access), `*_router.py` (thin handlers). Some also have `*_repository.py`, built on `app/common/repositories/base_repository.py`.
- **Routers** subclass `DefaultResponseRouter` (`app/common/router.py`), which defaults `response_model=ApiResponse`. Pass `response_model=None` explicitly for file downloads.
- **Response envelope:** always `{ success, message, data, detail }`. Build with `success_response()` and `error_response()` from `app/common/responses.py`. Typed payloads live in `app/common/response_models/`. Paginated `data` carries a `pagination` object (helpers in `app/common/utils.py`: `paginate_query`, `list_paginated`, `build_pagination_meta`).
- **Errors:** raise the `AppException` subclasses from `app/common/exceptions.py` (401/403/404/409/422). Handlers in `exception_handlers.py` convert them, plus validation errors and unhandled exceptions, into the same envelope. Don't return ad-hoc `HTTPException` bodies.
- **Auth and RBAC:** use the `Annotated` dependency aliases `CurrentUser`, `AdminUser`, `SuperAdminUser` and `CandidateUser` (from `app/components/auth/auth_dependencies.py`) and `DB` (from `app/core/dependencies.py`). Access tokens are HS256 JWTs. Refresh tokens are random, stored SHA-256 hashed in `refresh_tokens` with a TTL index. Password rules are in `app/common/validators.py::check_password_strength`, shared by every schema that accepts a password.
- **Enums and constants:** `app/common/constants/app_constants.py` (`UserRole`, `QuestionType`, `SubmissionStatus`, `MalpracticeType`, ...). The frontend mirrors these, so changing a value is a cross-repo change.
- **Interview runtime:** `candidate` handles start/answer/finish-round/screenshot/malpractice. `scoring` has background tasks (`scoring_tasks.py`), `proctoring` evaluates malpractice, `livekit` issues video tokens, and `websocket` (`connection_manager.py`, `ws_router.py`) pushes live updates to admins. Screenshots and other proctoring evidence are uploaded to S3 through `storage/s3_service.py` (the README's local `uploads/screenshots` storage is outdated). A scheduler (`scheduler/background.py`, APScheduler cron) runs `submission_reaper.py`, which auto-submits expired sessions, puts abandoned ones on hold and retries scoring (only for submissions whose `scoring_status` is pending, failed or needs_review). `REDIS_URL` is optional; leave it empty to run as a single process.
- **Indexes:** defined in the lifespan. A new unique or query-critical field needs an index added there.

## Testing

`test/conftest.py` provides an in-memory Mongo (`mongomock_motor`) as the `db` fixture, plus seeded `super_admin`, `admin_user`, `workspace` and similar fixtures. Rate limiting is disabled there via `RATELIMIT_ENABLED=0` and a patched limiter, which must happen before app modules are imported. `asyncio_mode = "auto"`, so async tests need no marker. Service-level tests are the norm (`test_<module>_service.py`), and `test_routers.py` covers HTTP. Mongomock doesn't implement every Mongo feature, so check query operators and aggregations against it.

## Gotchas

- `.env` exists locally and may contain real secrets. Never print it or commit it. `detect-secrets` runs in pre-commit against `.secrets.baseline`. Mark intentional test passwords with `# pragma: allowlist secret`.
- Ruff selects bandit (`S`), complexity (max 15) and naming rules. `B008` is ignored because FastAPI's `Depends()` in defaults is required. Tests are exempt from `S` and `N`.
- Startup raises `RuntimeError` by design if any of `JWT_SECRET_KEY`, `MONGODB_URL`, `DATABASE_NAME`, `S3_BUCKET_NAME` or `AWS_ACCESS_KEY_ID` is empty (see `core/lifespan.py`). Locally, a placeholder for the two AWS values is enough to boot; only S3 features then fail.
- **Pass/fail:** `assessments.pass_percentage` (default 50) is the only stored value. A submission's `result` is derived on read by `components/assessment/result.py` for completed submissions whose rounds are all scored, and is admin-only. Don't store it or add it to candidate-facing payloads. The overall `percentage` is total correct over total questions across all rounds.
