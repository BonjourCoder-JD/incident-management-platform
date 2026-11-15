# Project Specification — Security Incident Management Platform

Last updated: 2026-11-15 · Milestone 1 (authentication and RBAC) complete — commit `1ae693b`, CI run [43668840665](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43668840665) passed

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
             session cookie (HttpOnly) + CSRF header             sessions, users, incidents
```

| Layer | Technology | Location |
|---|---|---|
| Frontend | React 19, TypeScript (strict), Vite 6, no UI or routing libraries | `frontend/` |
| API | FastAPI, Pydantic v2 schemas | `backend/app/main.py`, `auth.py`, `users.py`, `incidents.py`, `schemas.py` |
| Security | Argon2id hashing (`argon2-cffi`), session tokens, login rate limiting | `backend/app/security.py`, `auth.py`, `config.py` |
| Persistence | SQLAlchemy 2 ORM, Alembic migrations | `backend/app/models.py`, `database.py`, `backend/migrations/` |
| Database | PostgreSQL 17 | `compose.yaml` service `db` |
| Admin tooling | `python -m app.cli create-admin` | `backend/app/cli.py` |
| Local runtime | Docker Compose; ports bound to `127.0.0.1` only | `compose.yaml` |
| CI | GitHub Actions | `.github/workflows/ci.yml` |

**Key design points**

- The frontend reaches the API only through the Vite proxy (`/api/*` → backend, prefix removed), so browser and API are same-origin. No API URL or credentials are compiled into the frontend.
- **Authorization is enforced in FastAPI.** The React UI hides controls a role cannot use, but that is a convenience only; every rule is checked on the server and covered by negative tests.
- The database schema is owned by Alembic migrations. CI fails if the ORM models and the migrations diverge (`alembic check`).
- Backend tests run against real PostgreSQL. Each test runs inside a transaction that is rolled back, so no test data is kept, and CI uses a disposable database.
- Frontend tests mock `fetch`, so they need neither the backend nor the database.

### Authentication and session design

| Topic | Decision | Trade-off |
|---|---|---|
| Session mechanism | **Server-side sessions in PostgreSQL.** The browser holds a 256-bit random token in an `HttpOnly; Secure; SameSite=Strict; Path=/` cookie (`imp_session`). Only its SHA-256 hash is stored. | One database lookup per request (cheap at this scale). In exchange: no token is readable by JavaScript or kept in `localStorage`, sessions are revocable immediately, and a database leak does not reveal usable tokens. Stateless JWTs were rejected because they cannot be revoked before expiry. |
| Expiry | Absolute lifetime 12 h, idle timeout 2 h (`SESSION_ABSOLUTE_HOURS`, `SESSION_IDLE_MINUTES`). Expired sessions are deleted when presented. | `last_seen_at` is written at most once a minute, so idle expiry is accurate to about a minute. |
| Revocation | Logout deletes the session server-side. Changing a user's role or deactivating them deletes all their sessions. Changing your password signs out your other sessions. | Users are signed out by role changes; they sign in again to get the new role. |
| CSRF | **Synchronizer token**: each session has a random CSRF token, returned by `/auth/login` and `/auth/me`, held in memory by the SPA and required in an `X-CSRF-Token` header on every `POST`/`PUT`/`PATCH`/`DELETE`. `SameSite=Strict` adds a second layer. Login itself only accepts `application/json`, which a cross-site HTML form cannot send. | A full page reload fetches the token again from `/auth/me`. |
| Passwords | **Argon2id** (`argon2-cffi` defaults, RFC 9106 low-memory profile), salted, upgraded automatically on login if parameters change. 12–128 characters, not only whitespace. | Hashing is deliberately slow (tens of milliseconds); the 128-character cap bounds the cost per request. |
| Account enumeration | Unknown email, wrong password and deactivated account all return the same 401 message, and unknown emails still run a dummy Argon2 verification so timing does not reveal which emails exist. | — |
| Login abuse | Failed logins are counted per email (5 per 15 min) and per client IP (30 per 15 min); beyond that, **429** with `Retry-After`, even for the right password. A successful login clears that email's count. | In-memory and per process (see ISSUE-7). |
| Credential hygiene | Validation errors never echo submitted values (FastAPI's default `input` field is stripped). Request logs contain method and path only. The bootstrap command never takes the password as an argument or prints it. | — |
| Bootstrap | No public sign-up. The first admin is created with `python -m app.cli create-admin --email …`, reading the password from a hidden prompt or `BOOTSTRAP_ADMIN_PASSWORD`. Admins create all other accounts. | Admins set temporary passwords; there is no forced change on first login yet (ISSUE-8). |

### Roles and permissions (approved)

| Action | Reporter | Responder | Admin |
|---|---|---|---|
| Create incidents | ✓ (owned by themselves) | ✓ | ✓ |
| View incidents | Only their own | All, including legacy | All, including legacy |
| Edit title, description, severity | Only their own | All | All |
| Change status | ✗ (403) | ✓ | ✓ |
| Assign / unassign | ✗ (403) | ✓ | ✓ |
| View assignee directory (`/users/assignees`) | ✗ (403) | ✓ | ✓ |
| Create users, change roles, deactivate | ✗ (403) | ✗ (403) | ✓ (not on themselves) |
| Change own password | ✓ | ✓ | ✓ |

- Incidents a reporter may not see return **404**, identical to a missing incident, so their existence is not revealed.
- Ownership (`reporter_id`) is always taken from the session; a client-supplied `reporter_id` is rejected (422).
- Assignees must be active responders or admins (otherwise 422).
- **The visibility rule lives in one function (`_visible_to` in `backend/app/incidents.py`). Audit history, analytics and CSV export must reuse it.**

### Data model

**`users`** (migration `0002`)

| Column | Type | Rules |
|---|---|---|
| `id` | integer, primary key | |
| `email` | varchar(254), unique | stored trimmed and lower-case (check constraint), so uniqueness is case-insensitive |
| `password_hash` | varchar(255) | Argon2id encoded hash; never returned by the API |
| `role` | enum `user_role` | `reporter`, `responder`, `admin`; default `reporter` |
| `is_active` | boolean | default true; inactive users cannot sign in and their sessions stop working |
| `created_at`, `updated_at` | timestamptz | |

**`auth_sessions`** (migration `0002`): `token_hash` (SHA-256, unique), `csrf_token`, `user_id` (FK, cascade delete), `created_at`, `last_seen_at`, `expires_at`.

**`incidents`** (migrations `0001`, `0002`)

| Column | Type | Rules |
|---|---|---|
| `id` | integer, primary key | |
| `title` | varchar(200) | required |
| `description` | text | required |
| `severity` | enum `incident_severity` | `low`, `medium`, `high`, `critical` |
| `status` | enum `incident_status` | `open`, `investigating`, `resolved`, `closed`; default `open` |
| `reporter_id` | integer, nullable, FK → `users` (`SET NULL`) | set from the session; `NULL` for legacy incidents |
| `assignee_id` | integer, nullable, FK → `users` (`SET NULL`) | API accepts 1–2147483647; must be an active responder or admin |
| `created_at`, `updated_at` | timestamptz | set by the database; `updated_at` refreshed on ORM update |

Indexes: `ix_incidents_status_severity`, `ix_incidents_reporter_id`, `ix_incidents_assignee_id`, `ix_auth_sessions_user_id`.

**Legacy data strategy (migration `0002`).** Every existing incident is preserved. Incidents created before accounts existed keep `reporter_id = NULL` and are visible to responders and admins only. Existing `assignee_id` values are set to `NULL`, because no users existed before this migration, so they could not refer to anyone; this step is not reversed by a downgrade. The downgrade otherwise removes the users and sessions tables, the ownership column, both foreign keys and the `user_role` type.

### API

All `/incidents` and `/users` endpoints require a session (**401** otherwise). State-changing requests also require the `X-CSRF-Token` header (**403** otherwise).

| Method | Path | Who | Behaviour |
|---|---|---|---|
| `GET` | `/health`, `/health/db` | Public | Liveness and database connectivity (503 if unavailable) |
| `POST` | `/auth/login` | Public | Body `{email, password}`. Sets the session cookie; returns `{user, csrf_token}`. 401 generic failure; 429 with `Retry-After` when rate-limited. |
| `POST` | `/auth/logout` | Any | Deletes the session and clears the cookie. 204. |
| `GET` | `/auth/me` | Signed in | `{user, csrf_token}` |
| `PUT` | `/auth/password` | Signed in | `{current_password, new_password}`. 204; signs out other sessions. 422 if the current password is wrong. |
| `POST` | `/incidents` | Any role | Create. `assignee_id` only for responders/admins (403 for reporters). Unknown fields, including `reporter_id`, rejected. 201. |
| `GET` | `/incidents` | Any role | List visible incidents newest first. `limit` 1–100 (default 20), `offset` ≥ 0, optional `severity`, `status`. |
| `GET` | `/incidents/{id}` | Any role | 404 if missing **or not visible** |
| `PATCH` | `/incidents/{id}` | Any role | Partial update. Reporters: own incidents, no `status`/`assignee_id` (403). Only `assignee_id` may be `null`. |
| `GET` | `/users/assignees` | Responder, admin | Active responders and admins as `{id, email}` |
| `GET` | `/users` | Admin | All accounts |
| `POST` | `/users` | Admin | Create `{email, password, role}`. 409 on duplicate email. |
| `PATCH` | `/users/{id}` | Admin | Change `role` and/or `is_active`. 409 when targeting yourself. Revokes the user's sessions when either changes. |

Incident responses include `reporter_id`, `assignee_id`, and `reporter` / `assignee` as `{id, email}` or `null`.

---

## Completed functionality

### Milestone 1 — Authentication and RBAC

Committed as `1ae693b52ed41bbcc274df03fcfa8d1ec6a64e22` on 2026-11-15 and verified in GitHub Actions run [43668840665](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43668840665): all four jobs passed (details in the CI row below).

| Functionality | Status | Evidence |
|---|---|---|
| Accounts with unique, case-insensitive emails and Argon2id password hashes | **Verified** | `test_security.py`, `test_auth.py::test_passwords_are_stored_as_argon2id_hashes`, `test_users.py` |
| Login and logout with hardened session cookie; server-side revocation; replayed cookies rejected | **Verified** | `test_auth.py` (cookie attributes, token stored only as hash, logout replay) |
| Absolute and idle session expiry; deactivated users' sessions stop working | **Verified** | `test_auth.py::test_expired_and_idle_sessions_are_rejected_and_deleted`, `::test_sessions_of_deactivated_users_stop_working` |
| CSRF protection on all state-changing requests: every non-`GET`/`HEAD`/`OPTIONS` method (including `DELETE`, for future endpoints) and logout; login is exempt by design (no session; JSON only) | **Verified** | `test_auth.py::test_every_state_changing_route_rejects_missing_or_wrong_csrf` (enumerates every route from the app, so new endpoints are covered automatically), `::test_csrf_route_inventory_is_complete`, `::test_csrf_check_treats_every_non_safe_method_as_state_changing`, `::test_logout_requires_csrf_token_for_a_live_session`, `::test_login_requires_json` |
| Generic login failures; login rate limiting with `Retry-After` | **Verified** | `test_auth.py`, `test_security.py::test_rate_limiter_sliding_window` |
| `/auth/me`, password change revoking other sessions | **Verified** | `test_auth.py` |
| Role enforcement on list, detail, create and update, including cross-user denial and legacy incidents | **Verified** | `test_authorization.py` |
| Ownership from the session only | **Verified** | `test_authorization.py::test_reporter_creates_incidents_owned_by_themselves` |
| Assignee must be an active responder or admin | **Verified** | `test_authorization.py::test_assignee_must_be_an_active_responder_or_admin` |
| Admin user management; no self-escalation or self-demotion; sessions revoked on role/status change | **Verified** | `test_users.py` |
| Admin bootstrap command (no hardcoded password; never printed) | **Verified** | `test_security.py` (CLI tests); manual run on a disposable stack |
| Validation errors never echo submitted values | **Verified** | `test_auth.py::test_login_validation_errors_do_not_echo_the_password`, `test_incident_api.py::test_validation_errors_do_not_echo_input` |
| Migration `0002` upgrade, downgrade to base, re-upgrade; models match migrations | **Verified** | Local CI-equivalent run on a disposable PostgreSQL; CI job "Migration round trip" |
| **BUG-1 fixed**: `assignee_id` 2147483648 → 422 on POST and PATCH; 2147483647 passes schema validation | **Verified** | `test_authorization.py::test_bug1_*` (3 tests); manual browser check |
| Frontend sign-in, sign-out, session-expiry handling, rate-limit message; requested URL kept through sign-in | **Verified** | `auth.test.tsx`; manual browser check |
| Role-aware navigation and screens (reporter: "My incidents", no status or assignee controls; admin: Users page; forbidden state for others) | **Verified** | `permissions.test.tsx`; manual browser check |
| Assignee picker (responders and admins); emails shown for reporter and assignee | **Verified** | `CreateIncidentPage.test.tsx`, `IncidentDetailPage.test.tsx`, `permissions.test.tsx` |
| Users page (create, change role, deactivate/reactivate; own row protected) and Account page (change password) | **Verified** | `UsersPage.test.tsx`, `AccountPage.test.tsx`; manual browser check |
| CSRF token kept in memory only; no tokens in `localStorage`, `sessionStorage` or `document.cookie` | **Verified (manual)** | Browser check, 2026-11-15 |

**Test-quality check:** each protection was deliberately disabled one at a time (15 variants: CSRF, visibility, triage rules, ownership, admin-only routes, self-demotion, session revocation on role change, BUG-1 bound, assignee rule, rate limiting, expiry, inactive login, logout, input echo, password-change revocation), plus two partial CSRF variants (exempting only `PUT`/`PATCH`, and disabling it entirely). Every variant made at least one test fail; the partial variant was caught on exactly the three affected routes.

**Manual browser check (2026-11-15):** 31/31 checks in Chromium against a disposable stack, covering every role, cross-user denial from the browser, cookie flags, CSRF refusal, session revocation on role change and deactivation, password change, rate limiting, BUG-1 and mobile layout. Backend logs contained no passwords, session tokens or CSRF tokens.

### Earlier milestones

| Area | Functionality | Status | Evidence |
|---|---|---|---|
| Backend | Health and database health endpoints | **Verified** | `test_health.py`; CI |
| Backend | Incident create, list, get, update | **Verified** | `test_incident_api.py` |
| Backend | Filtering by severity and status; limit/offset pagination with bounds | **Verified** | `test_incident_api.py::test_filter_and_pagination` |
| Backend | Input validation: enum values, empty title, unknown fields, null on required fields, 404s | **Verified** | `test_incident_api.py::test_invalid_inputs_and_not_found` |
| Database | Incident model persists with defaults and timestamps | **Verified** | `test_incident_model.py` |
| Frontend | Incident list with filters and pagination kept in the URL; create form; detail with status change and editing | **Verified** | page tests; manual browser checks |
| Frontend | Loading, empty, error and success states; API status indicator | **Verified** | page tests; manual check with backend stopped |
| Frontend | Responsive layout, light/dark themes, keyboard skip link; user-entered HTML rendered as text | **Verified (manual)** | Browser checks, 2026-11-14 and 2026-11-15 |
| CI | Backend tests on a disposable PostgreSQL; migration round trip including the admin bootstrap command; frontend tests, type check and build; Docker image builds | **Verified** | GitHub Actions run [43668840665](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43668840665) on `1ae693b`: Backend tests, Migration round trip (upgrade, downgrade, schema removed, re-upgrade, `alembic check`, bootstrap admin), Frontend tests and build, and Docker images all **success**. Earlier: run [43665186312](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43665186312) on `65d2185`. |
| Supply chain | Lockfile with `npm ci`; read-only CI token; no repository secrets | **Implemented** | `frontend/package-lock.json`, `ci.yml` |

**Test counts:** 72 backend (pytest), 60 frontend (Vitest + React Testing Library). Before Milestone 1: 6 and 35.

The manual browser checks were one-off Playwright scripts, not part of the repository or CI. Milestone 5 makes them permanent.

---

## Known issues

| ID | Issue | Impact | Resolution |
|---|---|---|---|
| ~~BUG-1~~ | ~~`assignee_id` above 2147483647 returned HTTP 500 instead of 422.~~ | — | **Fixed in Milestone 1** (`le=2147483647` on create and update schemas; regression tests). |
| ~~ISSUE-2~~ | ~~No authentication or authorization.~~ | — | **Resolved in Milestone 1.** |
| ISSUE-3 | `StarletteDeprecationWarning`: using `httpx` with `starlette.testclient` is deprecated in favour of `httpx2`. | Test warning only. | Milestone 6 dependency review. |
| ISSUE-4 | `/health/db` creates a new engine per request and duplicates a fallback database URL. | Inefficient; hardcoded fallback credential string. | Milestone 6. |
| ISSUE-5 | The frontend image runs the Vite dev server; containers run as root; migrations are not applied automatically on startup. | Not production-ready. | Milestone 6. |
| ISSUE-6 | When the backend is down, the Vite proxy returns 500, so the UI says "server error" rather than "unreachable". | Minor wording. | Milestone 6 (production proxy). |
| ISSUE-7 | The login rate limiter is in memory and per process, and behind the Vite proxy every browser shares the proxy's IP, so the per-IP limit is effectively global. | Limits reset on restart and are not shared across workers; many failures from anyone can briefly block all sign-ins. | Milestone 6: shared store (PostgreSQL or Redis) and trusted-proxy client IP handling. |
| ISSUE-8 | No self-service password reset, and no forced password change after an admin sets a temporary password. | Admins must share temporary passwords out of band. | Future milestone (needs email delivery); forced first-login change could come in Milestone 6. |
| ISSUE-9 | Expired session rows are deleted only when presented or when that user signs in again. | Slow growth of `auth_sessions`. | Milestone 6: periodic cleanup. |
| ISSUE-10 | Demoting a responder or admin to reporter does not unassign incidents already assigned to them. | Stale assignment visible to staff. | Decide policy in Milestone 2 (audit trail will record it). |
| ISSUE-11 | `Secure` cookies work over plain HTTP only on `localhost` (a browser exception). | Any other host must use HTTPS. `SESSION_COOKIE_SECURE=false` exists for local testing only. | Milestone 6 deployment uses HTTPS. |

---

## Remaining milestones

Milestones are delivered in order, each as a small, separately approved change with passing CI. **Each milestone keeps all earlier tests passing and adds tests for its own behaviour.** Milestones 2–4 must apply the same visibility rule as incident endpoints (reporters: own incidents only) and include negative tests for it.

### Milestone 2 — Audit trail · Planned

Every change to an incident is recorded and cannot be edited.

**Acceptance criteria**
- [ ] `incident_events` table (migration) storing incident ID, **authenticated actor** (user ID), **timestamp** (server-side, UTC), **action** (`created`, `updated`, `status_changed`, …), and **before/after values** of each changed field.
- [ ] Events are written in the same database transaction as the change, so a change is never saved without its audit record.
- [ ] No API exists to edit or delete audit events.
- [ ] `GET /incidents/{id}/events` returns the history, newest first, only to users allowed to view the incident (same visibility rule; 404 otherwise).
- [ ] Incident detail page shows a readable activity timeline.
- [ ] Tests prove: each create and update produces exactly one correct event; no-op updates produce none; actor and diff are correct; history respects permissions, including cross-reporter denial.

### Milestone 3 — Analytics dashboard · Planned

A dashboard of incident metrics computed in PostgreSQL.

**Acceptance criteria**
- [ ] Aggregation endpoint(s) computed with SQL aggregates (not by loading all rows into Python): counts by status and severity; open incidents by severity; incidents created per day or week over a selectable range; median time to resolve.
- [ ] Results apply the same visibility rule (reporters' figures cover only their own incidents).
- [ ] Indexes added where needed, with query plans checked for the main aggregates.
- [ ] Frontend dashboard page with accessible charts or tables (text equivalents for charts), loading, empty and error states, and responsive layout.
- [ ] Backend tests with seeded data assert exact aggregate values per role; frontend tests cover rendering and states.

### Milestone 4 — Secure CSV export · Planned

Export the incidents a user is allowed to see, using the active filters.

**Acceptance criteria**
- [ ] `GET /incidents/export.csv` accepts the same `severity` and `status` filters as the list and returns only incidents the caller may view (same visibility rule).
- [ ] Authentication required; permissions are identical to the list endpoint.
- [ ] **CSV injection prevented**: cells beginning with `=`, `+`, `-`, `@`, tab or carriage return are neutralised.
- [ ] Correct quoting of commas, quotes and newlines; UTF-8; `Content-Disposition: attachment` with a safe filename.
- [ ] Streamed output with a documented maximum row count, so a large export cannot exhaust memory.
- [ ] Frontend "Export CSV" button on the list that uses the current filters.
- [ ] Tests cover filters, permissions (including cross-reporter denial), escaping, injection neutralisation and headers.

### Milestone 5 — Playwright end-to-end tests in CI · Planned

Turn the manual browser verification into a permanent, automated suite.

**Acceptance criteria**
- [ ] `@playwright/test` suite in the repository covering: login per role, create, list with filters and pagination, detail edit and status change, audit timeline, dashboard, CSV export, and permission denials.
- [ ] Runs against the full Docker Compose stack with a disposable database seeded by fixtures (admin created with the bootstrap command), never a developer database.
- [ ] New CI job that runs the suite and uploads traces and screenshots on failure; no repository secrets needed.
- [ ] Includes mobile-viewport checks for no horizontal scrolling, and a basic automated accessibility scan of the main pages.
- [ ] Reliable: no fixed sleeps; passes repeatedly in CI.

### Milestone 6 — Production hardening, deployment and final documentation · Planned

**Acceptance criteria**
- [ ] Production frontend image: static build served by a minimal web server that also proxies `/api`; no dev server in production.
- [ ] Containers run as non-root users; minimal base images; health checks; migrations applied automatically and safely at deploy time.
- [ ] Security headers (Content-Security-Policy, HSTS when on HTTPS, `X-Content-Type-Options`, frame protections); CORS locked down; request size limits.
- [ ] Configuration only through environment variables, with a documented `.env.example`; no default credentials in production paths (resolves ISSUE-4).
- [ ] Shared, restart-safe login rate limiting with correct client IPs behind the proxy (ISSUE-7); expired-session cleanup (ISSUE-9); HTTPS-only cookies (ISSUE-11).
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
