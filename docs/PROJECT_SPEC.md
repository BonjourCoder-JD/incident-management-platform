# Project Specification — Security Incident Management Platform

Last updated: 2026-11-14 · Baseline commit: `65d2185`

A full-stack application for reporting, triaging and resolving security incidents. It is intended to be employer-ready: working end to end, tested automatically, secure by default, and documented.

## Status key

| Label | Meaning |
|---|---|
| **Implemented** | Code is on `main`. |
| **Verified** | Implemented, and covered by automated tests that run in CI, and/or exercised manually in a real browser against the real API (noted where manual). |
| **Planned** | Required, not started. Listed under [Remaining milestones](#remaining-milestones). |

Nothing below is marked Verified without a test or a recorded manual check.

---

## Architecture

```
Browser ──► Vite dev server (frontend, :5173) ──/api/* proxy──► FastAPI (backend, :8000) ──► PostgreSQL 17
             React 19 + TypeScript                               SQLAlchemy 2 + Alembic
```

| Layer | Technology | Location |
|---|---|---|
| Frontend | React 19, TypeScript (strict), Vite 6, no UI or routing libraries | `frontend/` |
| API | FastAPI, Pydantic v2 schemas | `backend/app/main.py`, `incidents.py`, `schemas.py` |
| Persistence | SQLAlchemy 2 ORM, Alembic migrations | `backend/app/models.py`, `database.py`, `backend/migrations/` |
| Database | PostgreSQL 17 | `compose.yaml` service `db` |
| Local runtime | Docker Compose; ports bound to `127.0.0.1` only | `compose.yaml` |
| CI | GitHub Actions | `.github/workflows/ci.yml` |

**Key design points**

- The frontend reaches the API only through the Vite proxy (`/api/*` → backend, prefix removed). No API URL or credentials are compiled into the frontend.
- The database schema is owned by Alembic migrations. CI fails if the ORM models and the migrations diverge (`alembic check`).
- Backend tests run against real PostgreSQL. Each test runs inside a transaction that is rolled back, so no test data is kept.
- Frontend tests mock `fetch`, so they need neither the backend nor the database.

### Data model (`incidents` table, migration `0001`)

| Column | Type | Rules |
|---|---|---|
| `id` | integer, primary key | |
| `title` | varchar(200) | required |
| `description` | text | required |
| `severity` | enum `incident_severity` | `low`, `medium`, `high`, `critical` |
| `status` | enum `incident_status` | `open`, `investigating`, `resolved`, `closed`; default `open` |
| `assignee_id` | integer, nullable | 32-bit (max 2147483647); not yet a foreign key (no users table) |
| `created_at`, `updated_at` | timestamptz | set by the database; `updated_at` refreshed on ORM update |

Index: `ix_incidents_status_severity (status, severity)`.

### API

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/health` | Liveness: `{"status":"ok"}` |
| `GET` | `/health/db` | Database connectivity; 503 if unavailable |
| `POST` | `/incidents` | Create. Body: `title`, `description`, `severity`, optional `assignee_id`. Unknown fields rejected. 201. |
| `GET` | `/incidents` | List newest first. `limit` 1–100 (default 20), `offset` ≥ 0, optional `severity`, `status`. Returns `{items, total, limit, offset}`. |
| `GET` | `/incidents/{id}` | Get one; 404 if missing |
| `PATCH` | `/incidents/{id}` | Partial update of `title`, `description`, `severity`, `status`, `assignee_id`. Only `assignee_id` may be `null`. 404 if missing. |

---

## Completed functionality

| Area | Functionality | Status | Evidence |
|---|---|---|---|
| Backend | Health and database health endpoints | **Verified** | `test_health.py`; CI |
| Backend | Incident create, list, get, update | **Verified** | `test_incident_api.py` (create/read/update/close) |
| Backend | Filtering by severity and status; limit/offset pagination with bounds | **Verified** | `test_incident_api.py::test_filter_and_pagination` |
| Backend | Input validation: enum values, empty title, unknown fields, null on required fields, 404s | **Verified** | `test_incident_api.py::test_invalid_inputs_and_not_found` |
| Database | Incident model persists with defaults and timestamps | **Verified** | `test_incident_model.py` |
| Database | Migration upgrade, downgrade to base (tables and enum types removed), re-upgrade; models match migrations | **Verified** | CI job "Migration round trip" |
| Frontend | Incident list with severity/status filters and pagination; filters kept in the URL | **Verified** | `IncidentListPage.test.tsx`; manual browser check |
| Frontend | Create form with client validation matching API rules; server 422 errors mapped to fields | **Verified** | `CreateIncidentPage.test.tsx`, `validation.test.ts`; manual browser check |
| Frontend | Incident detail with one-click status change and edit form (sends only changed fields; can clear assignee) | **Verified** | `IncidentDetailPage.test.tsx`; manual browser check |
| Frontend | Loading, empty, error (with Retry) and success states; API status indicator | **Verified** | page tests; manual check with backend stopped |
| Frontend | Responsive layout (cards under 640 px), light/dark themes, keyboard skip link | **Verified (manual)** | Playwright script run against a disposable stack, 2026-11-14 |
| Frontend | User-entered HTML rendered as text, not executed | **Verified (manual)** | Same manual run; React escaping, no `dangerouslySetInnerHTML` |
| CI | Backend tests on a disposable PostgreSQL; migration round trip; frontend tests, type check and build; backend and frontend Docker image builds | **Verified** | GitHub Actions run [43665186312](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43665186312) on `65d2185` |
| Supply chain | Frontend lockfile with `npm ci` installs; read-only CI token; no repository secrets | **Implemented** | `frontend/package-lock.json`, `ci.yml` |

**Test counts at baseline:** 6 backend (pytest), 35 frontend (Vitest + React Testing Library).

The 2026-11-14 manual browser check was a one-off Playwright script, not part of the repository or CI. Milestone 5 makes it permanent.

---

## Known issues

| ID | Issue | Impact | Resolution |
|---|---|---|---|
| BUG-1 | `assignee_id` values above **2147483647** produce **HTTP 500** instead of 422 on `POST /incidents` and `PATCH /incidents/{id}`. The schema enforces only `> 0`, but the column is a 32-bit integer. Reproduced 2026-11-14: `2147483647` → 201, `2147483648` → 500. | Unhandled server error from user input. The frontend blocks these values; direct API clients do not. | **Required in Milestone 1.** Add an upper bound (`le=2147483647`) in `IncidentCreate` and `IncidentUpdate`, plus regression tests: 422 for `2147483648` on POST and PATCH, and `2147483647` still accepted. |
| ISSUE-2 | No authentication or authorization. Anyone who can reach the API can read and change every incident. | Unsafe beyond `localhost`. | Milestone 1. |
| ISSUE-3 | `StarletteDeprecationWarning`: using `httpx` with `starlette.testclient` is deprecated in favour of `httpx2`. | Test warning only; no functional effect today. | Re-evaluate during Milestone 6 dependency review. |
| ISSUE-4 | `/health/db` creates a new engine per request and duplicates a fallback database URL. | Inefficient; hardcoded fallback credential string. | Milestone 6. |
| ISSUE-5 | The frontend image runs the Vite dev server; containers run as root; migrations are not applied automatically on startup. | Not production-ready. | Milestone 6. |
| ISSUE-6 | When the backend is down, the Vite proxy returns 500, so the UI says "server error" rather than "unreachable". | Minor wording. | Milestone 6 (production proxy). |

---

## Remaining milestones

Milestones are delivered in order, each as a small, separately approved change with passing CI. **Each milestone keeps all earlier tests passing and adds tests for its own behaviour.**

### Milestone 1 — Authentication and authorization · Planned

Users log in, and every API action is restricted by role. The roles are approved; the exact permissions below are a proposal to confirm before implementation starts.

| Role | Proposed permissions |
|---|---|
| `reporter` | Create incidents; view incidents they reported |
| `responder` | View all incidents; update status, severity, description and assignment |
| `admin` | Everything a responder can, plus manage users and roles |

**Acceptance criteria**
- [ ] `users` table via Alembic migration; passwords stored only as strong salted hashes (e.g. Argon2 or bcrypt), never logged or returned.
- [ ] Login issues a short-lived credential (an HttpOnly, Secure, SameSite session cookie or a short-lived token) with logout or expiry; signing secrets come from environment variables, not the repository.
- [ ] Every `/incidents` endpoint requires authentication (401 when missing or invalid) and enforces the role table above (403 when not permitted). `/health` stays public.
- [ ] Incidents record their reporter; `assignee_id` becomes a foreign key to `users`.
- [ ] Login attempts are rate-limited or otherwise protected against brute force.
- [ ] Frontend: login page, logout, current-user display, actions hidden or disabled when not permitted, and 401/403 handled gracefully.
- [ ] **BUG-1 fixed** with the regression tests described above.
- [ ] Backend tests cover 401, 403 and allowed paths for each role; frontend tests cover login and permission-aware UI.

### Milestone 2 — Audit trail · Planned

Every change to an incident is recorded and cannot be edited.

**Acceptance criteria**
- [ ] `incident_events` table (migration) storing incident ID, **authenticated actor** (user ID), **timestamp** (server-side, UTC), **action** (`created`, `updated`, `status_changed`, …), and **before/after values** of each changed field.
- [ ] Events are written in the same database transaction as the change, so a change is never saved without its audit record.
- [ ] No API exists to edit or delete audit events.
- [ ] `GET /incidents/{id}/events` returns the history, newest first, to users allowed to view the incident.
- [ ] Incident detail page shows a readable activity timeline.
- [ ] Tests prove: each create and update produces exactly one correct event; no-op updates produce none; actor and diff are correct; history respects permissions.

### Milestone 3 — Analytics dashboard · Planned

A dashboard of incident metrics computed in PostgreSQL.

**Acceptance criteria**
- [ ] Aggregation endpoint(s) computed with SQL aggregates (not by loading all rows into Python): counts by status and severity; open incidents by severity; incidents created per day or week over a selectable range; median time to resolve.
- [ ] Results respect role permissions (reporters see only their own incidents).
- [ ] Indexes added where needed, with query plans checked for the main aggregates.
- [ ] Frontend dashboard page with accessible charts or tables (text equivalents for charts), loading, empty and error states, and responsive layout.
- [ ] Backend tests with seeded data assert exact aggregate values; frontend tests cover rendering and states.

### Milestone 4 — Secure CSV export · Planned

Export the incidents a user is allowed to see, using the active filters.

**Acceptance criteria**
- [ ] `GET /incidents/export.csv` accepts the same `severity` and `status` filters as the list and returns only incidents the caller may view.
- [ ] Authentication required; permissions are identical to the list endpoint.
- [ ] **CSV injection prevented**: cells beginning with `=`, `+`, `-`, `@`, tab or carriage return are neutralised.
- [ ] Correct quoting of commas, quotes and newlines; UTF-8; `Content-Disposition: attachment` with a safe filename.
- [ ] Streamed output with a documented maximum row count, so a large export cannot exhaust memory.
- [ ] Frontend "Export CSV" button on the list that uses the current filters.
- [ ] Tests cover filters, permissions, escaping, injection neutralisation and headers.

### Milestone 5 — Playwright end-to-end tests in CI · Planned

Turn the manual browser verification into a permanent, automated suite.

**Acceptance criteria**
- [ ] `@playwright/test` suite in the repository covering: login per role, create, list with filters and pagination, detail edit and status change, audit timeline, dashboard, CSV export, and permission denials.
- [ ] Runs against the full Docker Compose stack with a disposable database seeded by fixtures, never a developer database.
- [ ] New CI job that runs the suite and uploads traces and screenshots on failure; no repository secrets needed.
- [ ] Includes mobile-viewport checks for no horizontal scrolling, and a basic automated accessibility scan of the main pages.
- [ ] Reliable: no fixed sleeps; passes repeatedly in CI.

### Milestone 6 — Production hardening, deployment and final documentation · Planned

**Acceptance criteria**
- [ ] Production frontend image: static build served by a minimal web server that also proxies `/api`; no dev server in production.
- [ ] Containers run as non-root users; minimal base images; health checks; migrations applied automatically and safely at deploy time.
- [ ] Security headers (Content-Security-Policy, HSTS when on HTTPS, `X-Content-Type-Options`, frame protections); CORS locked down; request size limits.
- [ ] Configuration only through environment variables, with a documented `.env.example`; no default credentials in production paths (resolves ISSUE-4).
- [ ] Structured logging without secrets or personal data; consistent error responses.
- [ ] Dependency review and CI dependency/vulnerability scanning; resolve ISSUE-3.
- [ ] Documented deployment to a chosen host, with a deploy workflow that uses a protected environment for any secrets.
- [ ] Final documentation: README with architecture diagram, screenshots, setup, testing, security model and roles, API reference, and a short "design decisions" section.

---

## Definition of done (every milestone)

- All existing backend, frontend and (once added) end-to-end tests pass locally and in GitHub Actions.
- New behaviour has tests, including failure and permission paths.
- No secrets, generated artifacts or unrelated files are committed.
- Documentation, including this file's status tables, is updated in the same milestone.
