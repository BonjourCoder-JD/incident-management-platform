# Project Specification — Security Incident Management Platform

Last updated: 2026-11-15 · Milestone 1 (authentication and RBAC) complete — commit `1ae693b`, CI run [43668840665](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43668840665) passed · Milestone 2 (audit trail) complete — commits `7838f9c` + `ec3ed2e`, CI run [43672524737](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43672524737) passed · Milestone 3 (analytics dashboard) complete — commit `7240e3a`, CI run [43714418907](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43714418907) passed · Milestone 4 (secure CSV export) complete — commit `02eba64`, CI run [43717432124](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43717432124) passed · Milestone 5 (end-to-end tests) complete — commits `13d3c34`, `7e65e81`, `dea4ba7`, CI run [43726750357](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43726750357) passed (all five jobs) · Roadmap expanded on 2026-11-15 (Milestones 5, 5A, 5B, 5C, 6 and a final presentation, all **Planned**)

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
| Audit trail | Append-only `incident_events` table, written in the same transaction as each incident change | `backend/app/audit.py`, migration `0003` |
| Analytics | PostgreSQL aggregates in one read-only snapshot; React dashboard with dependency-free SVG/CSS charts | `backend/app/analytics.py`, migration `0004`; `frontend/src/pages/DashboardPage.tsx`, `frontend/src/components/dashboard/` |
| End-to-end tests | Playwright (Chromium, 1 worker, no retries) against a disposable Compose project `imp-e2e` with random per-run passwords; axe accessibility checks | `e2e/` (`run.sh`, `compose.e2e.yaml`, `support/`, `tests/`); CI job "End-to-end tests" |
| CSV export | Streamed, formula-safe CSV from a read-only snapshot; every request recorded in an append-only table | `backend/app/exports.py`, migration `0005`; `frontend/src/components/ExportCsvButton.tsx` |
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
| View incident history | Own incidents only | All | All |

- Incidents a reporter may not see return **404**, identical to a missing incident, so their existence is not revealed.
- Ownership (`reporter_id`) is always taken from the session; a client-supplied `reporter_id` is rejected (422).
- Assignees must be active responders or admins (otherwise 422).
- **The visibility rule lives in one function (`_visible_to` in `backend/app/incidents.py`). Audit history (Milestone 2), every analytics query (Milestone 3, including the event-based resolution queries) and the CSV export's count and row queries (Milestone 4) use it.**
- Incident history follows incident visibility: reporters can read the history of their own incidents only (404 otherwise, including legacy incidents); responders and admins can read every incident's history.

### Audit trail design (Milestone 2)

| Topic | Decision | Notes and limits |
|---|---|---|
| Granularity | **Exactly one event per successful create or update request.** `action` is `status_changed` or `assignment_changed` when that is the only kind of field that changed, otherwise `updated`; creation is `created`. `changes` always lists every changed field. | A request that changes nothing records nothing and does not touch the row. |
| Recorded values | Only an **allowlist** of incident fields: `title`, `description` (full text before and after), `severity`, `status`, `assignee_id`. `record_incident_event` refuses any other key. | Passwords, hashes, session and CSRF tokens, request headers and IP addresses cannot enter the audit trail. Long descriptions are stored in full and collapsed in the UI. |
| Actor and time | Actor is the authenticated session user; time is the database's `now()` at insert. The API rejects `actor_id`, `action` or `changes` in request bodies (422). | — |
| Atomicity | The incident change and its event are added to the same SQLAlchemy session and committed together. If either write fails, both roll back. | Tested in both directions (event write fails; incident write fails) and for creation. |
| Concurrency | Updates read the incident with `SELECT … FOR UPDATE OF incidents` (and refresh any cached copy) before computing the diff, so concurrent updates are serialised and each event's `from` values are the ones actually persisted by the previous change. | Plain reads are not blocked. A second writer waits for the first to commit. |
| Immutability | No API can edit or delete events. A PostgreSQL trigger raises on `UPDATE`, `DELETE` and `TRUNCATE` of `incident_events`. Foreign keys to the incident and the actor are `RESTRICT`, so history never loses its references. | **Limits:** the table owner or a superuser can disable or drop the trigger (`ALTER TABLE … DISABLE TRIGGER` was demonstrated in a rolled-back transaction during verification), `session_replication_role = replica` skips row triggers, and downgrading migration `0003` drops the table and its history. Today the app connects as the table owner; running it as a non-owner role with only `INSERT`/`SELECT` on this table is part of Milestone 6 (ISSUE-12). |
| History start | **No backfill.** Changes made before migration `0003` have no trustworthy actor or time, so legacy incidents show an empty history with an explanation. | — |
| Reading | `GET /incidents/{id}/events` checks incident visibility first, then returns events newest first (`created_at`, then `id`), paginated, with the emails of people referenced by assignee changes. | — |
| Rendering | The UI shows every audited value as text through React (never as HTML) and falls back to plain strings for unknown fields. | Verified with an HTML/JavaScript payload in a title and description. |

### Analytics design (Milestone 3)

`GET /analytics/summary?from=YYYY-MM-DD&to=YYYY-MM-DD&tz=Area/City` returns every dashboard figure from **one** request.

| Topic | Decision | Notes and limits |
|---|---|---|
| Range | Inclusive calendar dates in the viewer's IANA time zone, applied as the **half-open** interval `[from 00:00, to + 1 day 00:00)` in that zone. Default: the last 30 days ending today in that zone; at most **366 days**; `from ≤ to`. | Errors are 422 with `loc` `["query", "from" \| "to" \| "tz"]`. |
| Time zone | Validated against PostgreSQL's `pg_timezone_names` (cached per process) and always passed as a bound parameter. The frontend sends the browser's zone. | An unknown or injection-style value is rejected before any query uses it. |
| Buckets | Daily up to 92 days, otherwise **ISO weeks** (Monday start, `date_trunc('week', …)` on local wall-clock time). The first and last weeks are clipped to the range and report their own `period_start`/`period_end`. Buckets are zero-filled. | Days follow local dates across DST changes. |
| Counts and breakdowns | Incidents **created** in the range (`incidents.created_at`, database-set and reliable for legacy incidents too), by their **current** status and severity. | — |
| Current open backlog | Open or investigating **right now**, by severity, **regardless of the range**. Labelled "Current open backlog" in the UI. | — |
| Resolution | An incident's **first** audit event whose status change moves it into `resolved` or `closed`, whether recorded as `status_changed` or inside a multi-field `updated` event. Time to resolution = that event's time − `incidents.created_at`. Median and 90th percentile (`percentile_cont`), overall and per severity, for incidents first resolved **in the range**; the resolved trend uses the same events. `reopened_count` counts those incidents ever moved from resolved/closed back to open/investigating. Never derived from `updated_at`. | Durations are returned in hours with 2 decimals (36 s). |
| Legacy incidents | Incidents with no `created` audit event (created before migration `0003`) are **excluded from all resolution metrics, even if later status changes were audited**, and counted in `excluded_legacy`; the UI explains the exclusion. They still count in totals, breakdowns and the created trend. | History before `0003` cannot be trusted to show the first resolution. |
| Visibility | Every query, including the first-resolution subquery over `incident_events`, is restricted with `_visible_to`. The response says `scope: "own"` for reporters and `"all"` for staff. | A structural test checks every query carries the reporter filter. |
| Consistency | Analytics uses a **dedicated** connection whose transaction starts with `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`, so all figures come from one snapshot and no write is possible. The setting is transaction-scoped: pooled connections and normal request sessions are unchanged (tested). | — |
| Query shape | 5 queries per request. First resolutions are found from resolution events **inside** the range with an anti-join on earlier ones (so a 30-day range reads only nearby events); the cohort is built once and overall, per-severity and per-bucket figures come from one `GROUPING SETS` pass; legacy status is a single `LEFT JOIN` on the `created` event. | Visibility is applied in both the subquery and the cohort. |
| Charts | Dependency-free SVG/CSS following the project's data-visualization method: one-hue meter lists for breakdowns (status and severity colours stay reserved for badges); a two-series line chart (categorical slots 1 and 2, validated for colour-vision deficiency and contrast against both card surfaces) with legend, direct end labels, crosshair tooltip on pointer **and keyboard**, a text summary, and a table view. Refetching keeps the previous figures dimmed. | Validator: worst CVD ΔE 24.7 (light), 26.8 (dark); contrast ≥ 3:1. |

**Index decision (migration `0004`), from `EXPLAIN ANALYZE` on 50,000 incidents and 225,547 audit events** (200 reporters, 5,000 legacy incidents; resolutions recorded as both `status_changed` and multi-field `updated` events; 10% reopened). Endpoint time, median of 5 runs, Europe/London:

| Indexes | Staff 30 d | Staff 366 d | Reporter 30 d | Reporter 366 d |
|---|---|---|---|---|
| Before `0004` | 59.6 ms | 154.7 ms | 33.8 ms | 30.4 ms |
| **`0004` (shipped)** | **44.5 ms** | **135.8 ms** | **30.3 ms** | **31.7 ms** |
| `0004` + `(reporter_id, created_at)` | 43.4 ms | 138.3 ms | 30.6 ms | 32.5 ms |

- **Added:** `ix_incidents_created_at` (used by every range filter and the created trend) and `ix_incident_events_first_resolution`, a partial index on `(incident_id, created_at) WHERE (changes -> 'status' ->> 'to') IN ('resolved', 'closed')` (used by both the in-range scan and the earlier-resolution anti-join; its predicate must match `analytics._resolution_event()`).
- **Not added:** a `(reporter_id, created_at)` index (the planner used it, but it changed timings by about 1 ms either way, so the existing `ix_incidents_reporter_id` is kept), and a time-ordered resolution index (never chosen by the planner).
- **Query restructuring mattered more than indexes:** the first design recomputed first resolutions in four separate queries (staff: 211 ms for 30 days, 400 ms for 366 days); computing the cohort once with `GROUPING SETS`, scanning only in-range resolution events and replacing repeated per-row legacy checks brought staff to the figures above.

### CSV export design (Milestone 4)

`GET /exports/incidents.csv?severity=&status=` (approved path; it avoids the dynamic `/incidents/{incident_id}` route).

| Topic | Decision | Notes and limits |
|---|---|---|
| Scope | Same `severity`/`status` filters and validation as the incident list (shared `incident_filters`), newest first, restricted with `_visible_to`: reporters export only their own incidents; responders and admins export everything. | Signed out: 401. Read-only route (`GET`); writes return 405. |
| Order of operations | 1) open a dedicated `REPEATABLE READ, READ ONLY` connection; 2) count the matching rows in that snapshot; 3) over the limit: record `rejected`, close, answer **413** before any data; 4) otherwise record `initiated` (committed) **before** the first byte; 5) stream. | The count and the rows come from the same snapshot, so `X-Export-Row-Count` matches the file. |
| Limit | `EXPORT_MAX_ROWS`, default **10,000**. Over the limit is refused with 413 and a message to narrow the filters; **never truncated**. | Exactly the limit is allowed (tested). |
| Format | Columns `id, title, description, severity, status, reporter_email, assignee_email, created_at_utc, updated_at_utc`; UTF-8 with BOM (for Excel); Python `csv` writer, minimal RFC 4180 quoting, **CRLF** record endings; timestamps ISO 8601 UTC (`Z`). | Embedded newlines stay inside quoted fields. |
| Headers | `Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="incidents-<date>[-<severity>][-<status>].csv"` (built only from the UTC date and validated enum values), `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, `X-Export-Row-Count`. | The browser saves under a name that passes a strict pattern check (`[A-Za-z0-9._-]+.csv`); anything else falls back to `incidents.csv`. |
| Formula injection | Every text cell (title, description, emails) gets a leading apostrophe when its **first character, or first non-whitespace character**, is one of `= + - @`, tab, CR, LF or the full-width `＝ ＋ － ＠`. Server-generated values (id, enums, timestamps) are never altered. | Limits are recorded as ISSUE-15. Benign text such as "A=B" is unchanged; text starting with "-" (e.g. "-5 °C") gains a visible apostrophe in raw CSV. |
| Streaming and memory | Server-side cursor (`stream_results`), batches of 500 rows. Peak Python memory measured at about **1.2 MB whether 3,000 or 50,000 rows** are streamed. | — |
| Cleanup | The export connection is closed by the stream's `finally` (end, error, or abandoned stream), by the endpoint when refusing or failing before streaming, and by a response background task; closing twice is harmless. | Tested on every path, including a real pooled connection returned to the pool after an abandoned stream. |
| Records | Append-only `incident_exports`: actor, outcome `initiated` or `rejected`, filters, matched row count, limit in force, time. A trigger blocks `UPDATE`, `DELETE` and `TRUNCATE`; the actor foreign key is `RESTRICT`. **A completed download is never claimed**: `initiated` means streaming began. | Same trigger limits as the audit trail (ISSUE-12). No review screen yet (ISSUE-16). |
| Frontend | "Export CSV" in the incident list's filter row, using the URL's filters; accessible name names the scope (e.g. "Export CSV (High, Open)"); busy state ("Preparing export…", disabled), live-region announcement and a visible "CSV download started — N incidents (file)" message (the app hands the file to the browser and never claims the save completed); 413 shown as "Too many incidents to export" without Retry; other errors with Retry; a 401 returns to sign-in. | The object URL is revoked and the temporary link removed after each download. |

**Benchmark (50,000 incidents, 225,547 events; staff unless noted):** High+Open 3,104 rows in 108 ms; 22,315 rows in 668 ms; all 50,000 rows in 1,584 ms (the last two above the limit, benchmark only); one reporter's 263 rows in 21 ms. About 32 µs per row, so a 10,000-row export takes roughly 0.3 s (interpolated, not measured at exactly 10,000). The count uses the existing `ix_incidents_status_severity` (3.2 ms); reporter exports use `ix_incidents_reporter_id`. **No new index was needed.**

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

Indexes: `ix_incidents_status_severity`, `ix_incidents_reporter_id`, `ix_incidents_assignee_id`, `ix_incidents_created_at` (migration `0004`), `ix_auth_sessions_user_id`.

**`incident_events`** (migration `0003`): append-only audit trail.

| Column | Type | Rules |
|---|---|---|
| `id` | integer, primary key | tie-breaker for ordering |
| `incident_id` | integer, FK → `incidents` (`RESTRICT`) | an incident with history cannot be deleted |
| `actor_id` | integer, FK → `users` (`RESTRICT`), not null | the signed-in user who made the change; never client input; an actor with history cannot be deleted |
| `action` | enum `incident_event_action` | `created`, `updated`, `status_changed`, `assignment_changed` |
| `changes` | `JSONB`, not null | `{"field": {"from": old, "to": new}}` for allowlisted fields only |
| `created_at` | timestamptz | set by the database at insert |

**`incident_exports`** (migration `0005`): append-only record of CSV export requests: `id`, `actor_id` (FK → `users`, `RESTRICT`), `outcome` (enum `export_outcome`: `initiated`, `rejected`), `filters` (JSONB `{severity, status}`), `row_count`, `row_limit`, `created_at`. Index `ix_incident_exports_actor_id_created_at`. A trigger rejects `UPDATE`, `DELETE`, `TRUNCATE`.

Indexes: `ix_incident_events_incident_id_id (incident_id, id)`, `ix_incident_events_actor_id`, and the partial `ix_incident_events_first_resolution (incident_id, created_at) WHERE status moved to resolved/closed` (migration `0004`).

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
| `GET` | `/incidents/{id}/events` | Any role (visibility applies) | Audit history newest first. `limit` 1–100 (default 20), `offset` ≥ 0. Returns `{items, total, limit, offset, users}`; each item is `{id, action, changes, actor: {id, email}, created_at}`. 404 when the incident is missing or not visible. No write methods (405). |
| `GET` | `/exports/incidents.csv` | Any role (visibility applies) | Optional `severity`, `status`. Streams the CSV described in *CSV export design*. 413 above `EXPORT_MAX_ROWS` (default 10,000) with `X-Export-Row-Count`; 422 for invalid filters. Each request is recorded in `incident_exports`. Read-only (405 for writes). |
| `GET` | `/analytics/summary` | Any role (visibility applies) | `from`, `to` (inclusive dates), `tz` (IANA). Returns `{scope, range, totals, by_status, by_severity, open_backlog_by_severity, trend, resolution}`; see *Analytics design*. 422 for invalid ranges or time zones. Read-only (405 for writes). |
| `GET` | `/users/assignees` | Responder, admin | Active responders and admins as `{id, email}` |
| `GET` | `/users` | Admin | All accounts |
| `POST` | `/users` | Admin | Create `{email, password, role}`. 409 on duplicate email. |
| `PATCH` | `/users/{id}` | Admin | Change `role` and/or `is_active`. 409 when targeting yourself. Revokes the user's sessions when either changes. |

Incident responses include `reporter_id`, `assignee_id`, and `reporter` / `assignee` as `{id, email}` or `null`.

---

## Completed functionality

### Milestone 5 — Permanent Playwright end-to-end tests

Released on 2026-11-15 in three commits:

| Commit | Contents |
|---|---|
| `13d3c3417c62aca1590e84e76e9ec5e2b46b2ee4` | `fix: meet WCAG contrast and label-in-name requirements` (DEF-1, DEF-2) |
| `7e65e81116ba56dafefb9b7ff94ae21c97253230` | `test: add Playwright end-to-end suite and CI job` (suite, CI job, roadmap expansion) |
| `dea4ba72496f52dbf4f638ee7f76925c52887401` | `test: wait for render before the E2E skip-link focus check` |

**CI record:**
- Run [43724759395](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43724759395) on `7e65e81` (the first two commits, pushed together): **failure**. Backend tests, Migration round trip, Frontend tests and build, and Docker images passed; End-to-end tests failed one test (37 passed): `the first Tab stop is the skip link` found the skip link never focused. The test pressed Tab immediately after `page.goto`, which resolves on the load event and can precede React's first render. The failure-evidence artifact uploaded as designed (55 KB). The race did not reproduce locally, even under 6x CPU throttling, so the cause is the most likely explanation rather than a proven one. Fix in `dea4ba7`: wait for the page heading before pressing Tab, and report the actually focused element if the check fails; assertion unchanged, no application change. Locally the fixed test passed 10 of 10 repeats and the full suite passed 38 of 38.
- Run [43726750357](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43726750357) on `dea4ba7`: **success**, all five jobs: Backend tests (48 s), Migration round trip (34 s), Frontend tests and build (26 s), Docker images (31 s), End-to-end tests (194 s; **38 passed** in 37.7 s of test time).

**Design.** `e2e/run.sh` (used by CI and locally) starts Compose project `imp-e2e` with `e2e/compose.e2e.yaml` (frontend on `127.0.0.1:15173`, API unpublished, `EXPORT_MAX_ROWS=5`), generates random database and admin passwords per run (masked in CI, never written to disk, taking precedence over a developer `.env`), migrates, creates the admin with the bootstrap command, seeds two synthetic legacy incidents, runs Playwright, and always removes the stack. Before any `down --volumes` it verifies that every volume labelled for the project is named `imp-e2e_*`; development volumes belong to a different project and are never in scope. Tests create their own synthetic users (random passwords, `example.com` addresses) and data, so they are independent of order and of each other. Selectors are roles, labels and visible text; waits are web-first assertions with no fixed sleeps; time zone is fixed to Europe/London. In CI, traces and the HTML report are off (they can contain request bodies and typed values); only failure screenshots, page snapshots (`error-context.md`) and the backend log are uploaded, for 7 days.

| Functionality | Status | Evidence |
|---|---|---|
| Sign-in and sign-out, generic error, deep link kept, 401 without session, 403 without CSRF token, hardened cookie and no web storage, role change and deactivation end sessions, password change signs out other devices, login rate limiting, admin account management without self-demotion | **Verified** | `e2e/tests/auth.spec.ts` (8 tests) |
| Create with validation, filters and pagination in the URL with Back, responder status/edit/assignment visible to the reporter, not-found routes, HTML shown as text, skip link, no-op saves | **Verified** | `e2e/tests/incidents.spec.ts` (7 tests) |
| Reporter UI limits, API refusals (403/422), cross-reporter and legacy 404s for detail, update and history, staff see everything | **Verified** | `e2e/tests/authorization.spec.ts` (4 tests) |
| History entries with correct actors, collapsed description diff, "Show older", legacy explanation, HTML as text, no-op and spoofed-actor protection | **Verified** | `e2e/tests/history.spec.ts` (5 tests) |
| Dashboard: exact reporter figures and isolation, multi-field resolution counted, legacy exclusion explained, presets/custom ranges/range errors in the URL, keyboard tooltip and table | **Verified** | `e2e/tests/dashboard.spec.ts` (4 tests) |
| CSV export: BOM, header, own rows only, formula neutralised, quoting and Unicode, headers, filters and filename, exactly-at-limit allowed and over-limit refused | **Verified** | `e2e/tests/export.spec.ts` (3 tests) |
| axe WCAG 2.1 A/AA scan (fails on serious/critical) of sign-in, list, detail, create with errors, dashboard, users and account pages in light and dark themes; no horizontal scrolling at 390 px | **Verified** | `e2e/tests/accessibility.spec.ts` (7 tests) |
| Every test fails on unexpected browser console errors | **Verified** | automatic fixture in `e2e/support/fixtures.ts` |

**Results (local, 2026-11-15):** 38 tests; **three consecutive runs, 38/38 each**, on freshly built stacks; about 1.1 minutes of test time and 169–170 s per run including build and teardown. Development containers and volumes were unchanged before and after (volume list compared; database container identical).

**Deliberate-break checks:** four application protections were removed in scratch copies and the suite rerun: reporter visibility (8 tests failed), CSV formula guard (1), CSRF check (1), export limit (1). All four caught.

**Failure path:** a deliberately failing local test produced a screenshot, page snapshot, local trace and backend log; `run.sh` exited non-zero and still removed the stack's containers, volume and network; no passwords, session cookies, CSRF tokens or bootstrap secrets appeared in the files CI would upload.

**Defects found and fixed (application changes, reported separately):**
- **DEF-1 (WCAG 1.4.3 contrast, serious):** light-theme "High" severity and "Admin" role badges used `#c2410c`, 4.34:1 (4.02:1 on the detail page) against the badge tint, below 4.5:1. Changed the light-theme `--sev-high` to `#9a3412`. Dark theme was already compliant.
- **DEF-2 (WCAG 2.5.3 label in name, serious):** the header account link's `aria-label` ("Account settings for <email>") replaced its visible text (email and role). Removed the `aria-label`; a visually hidden "Account settings for" now precedes the visible text, so the accessible name is "Account settings for <email> <Role>". The frontend test asserting the name was updated accordingly.

**Also found during implementation:** `@axe-core/playwright` accepts any `playwright-core`, so npm installed a second, two-day-old `playwright-core` 1.65.1 next to 1.64.1. An `overrides` entry pins a single 1.64.1.

**Dependencies (dev-only, `e2e/` package):** `@playwright/test` 1.64.1, `@axe-core/playwright` 4.14.0, `@types/node` 24.19.2, `typescript` 5.8.3; `npm audit` 0 vulnerabilities.

### Milestone 4 — Secure CSV export

Committed as `02eba64d892cf5a5ad88e2dbe34be122728d9c7c` on 2026-11-15 and verified in GitHub Actions run [43717432124](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43717432124) on the first attempt: Backend tests, Migration round trip (upgrade through `0005`, downgrade to base, schema removed, re-upgrade, `alembic check`, bootstrap command), Frontend tests and build, and Docker images all **success**.

| Functionality | Status | Evidence |
|---|---|---|
| Headers, BOM, CRLF, 9 columns, quoting, Unicode and UTC timestamps round-trip | **Verified** | `test_export.py::test_headers_bom_crlf_and_columns`, `::test_quoting_unicode_and_timestamps_round_trip`, `::test_empty_export_has_only_the_header`; browser download parsed |
| Formula-injection defence in every text column (16 payloads: `= + - @`, tab, CR, LF, `HYPERLINK`, DDE-style, leading spaces/tab/ideographic space, full-width forms); harmless text and server values untouched | **Verified** | `::test_formula_triggers_are_neutralised_in_every_text_column` (16 cases), `::test_harmless_text_is_unchanged` (7 cases), `::test_server_generated_values_are_not_altered`; browser download |
| Same filters as the list, newest first, filter-named filename, 422 for invalid filters | **Verified** | `::test_filters_and_newest_first` |
| Reporters export only their own incidents (no other reporter's or legacy rows, emails or text); staff export everything | **Verified** | `::test_reporters_export_only_their_own_incidents`, `::test_staff_export_everything`, `::test_export_query_applies_the_visibility_rule`; browser check with two reporters |
| 401 signed out; read-only route | **Verified** | `::test_requires_authentication_and_is_read_only` |
| Over-limit refused with 413 before streaming, never truncated; exactly the limit allowed | **Verified** | `::test_over_limit_is_refused_before_streaming_and_never_truncated`; browser check at a limit of 5 (5 rows allowed, 6 refused) |
| Every export recorded (`initiated` before streaming, `rejected` when refused) with actor, filters, row count and limit; records append-only | **Verified** | `::test_every_export_is_recorded_with_actor_and_filters`, `::test_export_records_are_append_only` (3 cases); `psql` on the disposable stack |
| Connection closed after success, refusal, failure before streaming, failure mid-stream and an abandoned stream; dedicated read-only `REPEATABLE READ` snapshot | **Verified** | `::test_connection_closed_after_successful_stream`, `::test_connection_closed_when_failing_before_streaming`, `::test_connection_closed_and_export_recorded_when_stream_fails`, `::test_abandoned_stream_closes_the_real_connection`, `::test_real_export_connection_is_a_read_only_repeatable_read_snapshot` |
| Migration `0005` upgrade, downgrade to base, re-upgrade; `alembic check` | **Verified** | CI-equivalent run on a disposable PostgreSQL |
| Export button: current filters, `text/csv` request with session cookie, server filename (unsafe names rejected), object URL revoked and link removed, busy state, success and 413 messages, Retry, 401 hand-off, reporter access | **Verified** | `ExportCsvButton.test.tsx` (13 tests); browser check with real downloads |

**Test-quality check:** 18 backend export rules were deliberately broken one at a time (visibility on rows and on the count; filters; first-visible-character check; full-width triggers; email cells; BOM; CRLF; limit off by one; limit not enforced; initiated and rejected records; closing on stream end and on pre-stream failure; read-only snapshot; `no-store`; `nosniff`; UTC timestamps): all 18 caught. 8 frontend behaviours were broken the same way (filters, filename check, URL revocation, link removal, Retry on 413, busy state, accessible name, `Accept` header): all 8 caught.

**Manual browser check (2026-11-15):** 16/16 checks in Chromium on a disposable stack with `EXPORT_MAX_ROWS=5`, using real downloads parsed in the test: two reporters and a responder; a `HYPERLINK` formula title neutralised; commas, quotes, newlines and emoji intact; filtered export matching the API list; refusal message at 6 rows; phone width.

**Regression-test reliability improvement:** the Milestone 3 test `shows loading, then every figure with clear labels` had a latent timing race (it waited for the role-based heading instead of the data). It failed 2 of 5 local runs and passed CI only by chance; it now waits for data-dependent content and passed 8 of 8 local runs. No assertion was removed.

**Dependency checks:** `npm audit` 0 vulnerabilities; `pip-audit -r backend/requirements.txt` no known vulnerabilities. No new dependencies.

### Milestone 3 — Analytics dashboard

Committed as `7240e3a58cab6dbb0069d6f0fb5e51b1b2c1ae94` on 2026-11-15 and verified in GitHub Actions run [43714418907](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43714418907) on the first attempt: Backend tests, Migration round trip (upgrade through `0004`, downgrade to base, schema removed, re-upgrade, `alembic check`, bootstrap command), Frontend tests and build, and Docker images all **success**.

| Functionality | Status | Evidence |
|---|---|---|
| Exact counts, breakdowns, zero-filled trends and current open backlog | **Verified** | `test_analytics.py::test_counts_and_breakdowns_use_creation_date_and_current_state`, `::test_daily_trend_is_zero_filled`, `::test_empty_range_returns_zeros_and_no_timings` |
| Half-open ranges in the viewer's time zone; local dates across DST; ISO weeks clipped to the range; day/week switch at 92/93 days | **Verified** | `::test_range_is_half_open_in_the_viewer_time_zone` (Asia/Tokyo boundary instants), `::test_daily_buckets_follow_local_dates_across_dst` (America/New_York), `::test_weekly_buckets_are_iso_weeks_clipped_to_the_range`, `::test_92_days_is_daily_and_93_days_is_weekly` |
| Resolution from audit events: first resolution, `closed` counts, multi-field `updated` events count, reopened counted, earlier resolutions excluded, exact median/p90 | **Verified** | `::test_resolution_metrics_come_from_audit_events`, `::test_status_change_inside_an_updated_event_counts_as_resolution` |
| Legacy incidents excluded from resolution metrics even with partial later history, and counted | **Verified** | `::test_legacy_incidents_are_excluded_from_timings_but_counted`; browser check |
| Cross-reporter isolation of every figure (counts, backlog, trend, timings, reopened), staff see all | **Verified** | `::test_reporters_see_only_their_own_incidents_and_events`, `::test_staff_scope`, `::test_analytics_queries_apply_the_visibility_rule`; browser check |
| Validation (order, 366-day limit, malformed dates, unknown or injection-style time zones), defaults, 401, no write methods | **Verified** | `::test_invalid_ranges_are_rejected` (6 cases), `::test_366_days_is_allowed_and_defaults_cover_30_days`, `::test_requires_authentication`, `::test_no_write_methods` |
| Dedicated `REPEATABLE READ, READ ONLY` snapshot that leaves pooled connections unchanged | **Verified** | `::test_real_dependency_uses_a_read_only_repeatable_read_snapshot` |
| Migration `0004` upgrade, downgrade to base, re-upgrade; `alembic check` including the partial index | **Verified** | CI-equivalent run on a disposable PostgreSQL |
| Dashboard: tiles, meter lists, trend chart with keyboard tooltip and table view, presets and custom ranges in the URL, browser time zone, dimmed refetch, empty/error/422 states, reporter "My dashboard" | **Verified** | `DashboardPage.test.tsx` (10 tests, loading states held with `deferred()`); browser check |

**Test-quality check:** 15 backend rules were deliberately broken one at a time (visibility on the first-resolution subquery, the cohort, counts and backlog; legacy exclusion; counting `updated` events; earlier resolutions; first vs latest resolution; half-open range end; viewer-zone buckets; partial-week clipping; read-only snapshot; range limit; time zone validation; reopened counting): all 15 caught. 8 frontend behaviours were broken the same way: 7 were caught at first; the miss (time zone hard-coded to UTC, invisible because the test runner's zone is UTC) led to `uses the browser time zone, not UTC, for the range and its days`, which now catches it.

**Manual browser check (2026-11-15):** 19/19 checks in Chromium on a disposable stack: staff, two reporters and a legacy incident; UI figures compared against the API's own response; isolation between reporters; a resolution recorded through a multi-field update; keyboard tooltip and table view; presets, custom ranges and a server range error; dark and light themes using the validated colours; phone width without horizontal scrolling.

**Dependency checks:** `npm audit` 0 vulnerabilities; `pip-audit -r backend/requirements.txt` no known vulnerabilities. No new dependencies.

### Milestone 2 — Incident audit trail

Committed as `7838f9c232170cf8f7a8f592ed1a813d5c702d34` on 2026-11-15. Its CI run [43672248889](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43672248889) **failed** in the frontend job: one new test asserted the "Loading history…" state synchronously, and on the CI runner the mocked response arrived first (a timing race in the test, not an application fault; backend, migration and Docker jobs passed). Fixed in `ec3ed2e415f1f5c473f8081e7126d1f060d11327`, which holds mocked responses until each loading state has been asserted, in that test and in two older loading tests with the same latent race. The suite then passed five consecutive local runs, and CI run [43672524737](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43672524737) passed all four jobs: Backend tests, Migration round trip (including `0003` downgrade and the bootstrap command), Frontend tests and build, and Docker images.

| Functionality | Status | Evidence |
|---|---|---|
| `created` event on every incident creation, with the session user as actor and all initial values | **Verified** | `test_audit.py::test_create_records_one_created_event_with_the_session_actor`, `::test_responder_create_with_assignee_records_the_assignee` |
| Exactly one correctly classified event per update; every changed field kept; no event for no-op updates | **Verified** | `test_audit.py::test_each_update_records_exactly_one_correctly_classified_event` (5 cases), `::test_noop_update_records_nothing_and_does_not_touch_the_row`, `::test_unassign_records_previous_assignee`, `::test_full_description_is_kept_before_and_after` |
| Actor is whoever made the change; client cannot supply actor or audit values | **Verified** | `test_audit.py::test_actor_is_whoever_made_the_change_not_the_reporter`, `::test_client_cannot_supply_actor_or_audit_values` |
| Only allowlisted fields recorded; no secrets | **Verified** | `test_audit.py::test_audit_records_only_allowlisted_fields`; database scan during the browser check found no secret-like values or other keys |
| History visibility per role, including legacy incidents; 401 when signed out | **Verified** | `test_audit.py::test_history_visibility_by_role`; browser check |
| Pagination and newest-first ordering; emails for assignee changes; no write methods | **Verified** | `test_audit.py::test_history_is_paginated_newest_first_with_actor_and_users`, `::test_history_has_no_write_endpoints` |
| Atomic writes: a failed event write rolls back the update or creation; a failed incident write rolls back the event | **Verified** | `test_audit.py::test_failed_event_write_rolls_back_the_update`, `::test_failed_event_write_rolls_back_the_creation`, `::test_failed_incident_write_rolls_back_the_event` |
| Immutability trigger rejects `UPDATE`, `DELETE`, `TRUNCATE`; incident and actor references cannot be deleted | **Verified** | `test_audit.py::test_database_rejects_changes_to_audit_events` (4 cases), `::test_actor_and_incident_references_are_preserved`; `psql` checks on a disposable stack |
| Concurrent updates serialised by a row lock | **Verified** | `test_audit.py::test_update_locks_the_incident_row` (SQL contains `FOR UPDATE OF incidents`), `::test_concurrent_writers_are_serialised_by_the_row_lock` (two real transactions; the second writer times out waiting) |
| Migration `0003` upgrade, downgrade to base, re-upgrade; models match migrations | **Verified** | CI-equivalent run on a disposable PostgreSQL |
| History section: plain-language entries, collapsed description diffs, loading, empty, error with Retry, "Show older", live refresh after edits, safe text rendering | **Verified** | `IncidentHistory.test.tsx` (8 tests); browser check |

**Test-quality check:** 11 audit protections were deliberately disabled one at a time (row lock, history visibility, update auditing, creation auditing, commit order, actor source, no-op handling, allowlist guard, classification, before-value timing, ordering). The first run missed one (actor taken from the reporter), because every test's actor was also the reporter; `test_actor_is_whoever_made_the_change_not_the_reporter` was added and now catches it. All 11 are caught.

**Manual browser check (2026-11-15):** 18/18 checks in Chromium on a disposable stack covering reporter, second reporter and responder flows, a legacy incident, an HTML payload, pagination over 17 events and mobile layout, followed by direct `psql` checks of the trigger and stored data.

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
| CI | Backend tests on a disposable PostgreSQL; migration round trip including the admin bootstrap command; frontend tests, type check and build; Docker image builds | **Verified** | Latest: GitHub Actions run [43726750357](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43726750357) on `dea4ba7` (Milestone 5): all **five** jobs **success**, including End-to-end tests (38 passed). Milestone 4: run [43717432124](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43717432124) on `02eba64`: all four jobs **success**. Milestone 3: run [43714418907](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43714418907) on `7240e3a`: all four jobs **success**. Milestone 2: run [43672524737](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43672524737) on `ec3ed2e`: all four jobs **success**. Milestone 1: run [43668840665](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43668840665) on `1ae693b`: Backend tests, Migration round trip (upgrade, downgrade, schema removed, re-upgrade, `alembic check`, bootstrap admin), Frontend tests and build, and Docker images all **success**. Earlier: run [43665186312](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43665186312) on `65d2185`. |
| Supply chain | Lockfile with `npm ci`; read-only CI token; no repository secrets | **Implemented** | `frontend/package-lock.json`, `ci.yml` |

**Test counts:** 165 backend (pytest), 91 frontend (Vitest + React Testing Library), 38 end-to-end (Playwright). After Milestone 3: 122 and 78. After Milestone 2: 98 and 68. After Milestone 1: 72 and 60. Before Milestone 1: 6 and 35.

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
| ISSUE-10 | Demoting a responder or admin to reporter does not unassign incidents already assigned to them. | Stale assignment visible to staff. | Decided in Milestone 2: deferred to Milestone 6 (automatic unassignment on demotion or deactivation, recorded as `assignment_changed` events with the admin as actor). |
| ISSUE-12 | The app connects to PostgreSQL as the owner of `incident_events`, so a compromised app or operator with that role could disable the immutability trigger. | Audit immutability is enforced against the application's own SQL and accidental changes, not against a malicious database owner. | Milestone 6: run the app as a role with only `INSERT`/`SELECT` on `incident_events` and no ownership; consider shipping events to external append-only storage. |
| ISSUE-11 | `Secure` cookies work over plain HTTP only on `localhost` (a browser exception). | Any other host must use HTTPS. `SESSION_COOKIE_SECURE=false` exists for local testing only. | Milestone 6 deployment uses HTTPS. |
| ISSUE-13 | The analytics "reopened" set is built from every audited reopening on each request (about 19 ms at 225k events), and PostgreSQL underestimates row counts for the JSONB status predicates, so the staff 366-day cohort uses a nested loop. | Fine at the benchmarked scale (staff 366 days: 136 ms at 50k incidents) but grows with event volume. | Re-benchmark in Milestone 6 at production-like volume; options are a partial index for reopen events, extended statistics, or a periodically refreshed summary table. |
| ISSUE-14 | Analytics read the same primary database as the application. | Heavy dashboard use competes with incident writes. | Milestone 6: consider a read replica or response caching if load requires it. |
| ISSUE-15 | CSV formula-injection defence has spreadsheet-specific limits: the leading apostrophe is hidden by Excel but may appear as a visible character in other tools (some LibreOffice and Google Sheets import paths, text editors); it protects the exported file only, not data copied out of it or re-saved without the prefix; and it targets the OWASP trigger set (`= + - @`, tab, CR, LF and their full-width forms, also after leading whitespace). | A spreadsheet user could still be tricked if they remove the apostrophe or paste the cell elsewhere. | Document for users in Milestone 6; revisit if a target application with other triggers is adopted. |
| ISSUE-16 | CSV exports are capped per request (10,000 rows) and every request is recorded, but there is no per-user rate limit, alerting, or screen for reviewing `incident_exports`. | Repeated exports are detectable only by querying the database. | Milestone 6: export rate limiting and an admin view or alert for unusual export volume. |
| ISSUE-17 | The end-to-end suite runs in Chromium only, against the Vite development server, and does not cover API-unavailable states or exact theme colours (covered by frontend unit tests and screenshots instead). | Browser-specific or production-build regressions would not be caught end to end. | Milestone 6: run the suite against the production frontend image; consider Firefox and WebKit projects. |

---

## Remaining milestones

Milestones are delivered in order, each as a small, separately approved change with passing CI. **Each milestone keeps all earlier tests passing and adds tests for its own behaviour.** Milestones 2–4 must apply the same visibility rule as incident endpoints (reporters: own incidents only) and include negative tests for it.

### Approved sequence (2026-11-15)

| Order | Milestone | Status |
|---|---|---|
| 1–4 | Authentication/RBAC, audit trail, analytics dashboard, secure CSV export | **Complete** (see *Completed functionality*) |
| 5 | Permanent Playwright end-to-end tests in GitHub Actions | **Complete** (`13d3c34`, `7e65e81`, `dea4ba7`) |
| 5A | Incident comments and internal-only responder notes | Planned |
| 5B | SLA tracking, response/resolution deadlines, overdue indicators and escalation | Planned |
| 5C | AI-assisted incident triage with human approval | Planned |
| 6 | Production hardening, observability, monitoring and deployment | Planned |
| — | Final presentation: live demo, sample data, screenshots, architecture diagram, README, 60–90 s walkthrough | Planned |

Technical additions listed under planned milestones are **anticipated designs, not implemented features**. Each is refined, and may change, in that milestone's approved plan.

### Deployable-core checkpoint

Commit `198f6be` (Milestones 1–4, CI run [43717555312](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43717555312) passed) is the **deployable core**: authentication and RBAC, incident create/read/update, the append-only audit trail, analytics and secure CSV export, with backend, frontend, migration and Docker checks in CI. It is functionally complete but **not yet production-hardened** (see ISSUE-3 to ISSUE-16, largely addressed in Milestone 6). Later milestones build on this core and must not regress it: its tests stay green, its security properties stay intact, and its migrations stay reversible.

### Approval gates and standing requirements (all remaining milestones)

- **Gates:** plan → explicit approval → implementation → local verification report → explicit approval → commit and push → CI verified → separate documentation commit recording the CI run (evidence moves from "Verified (local)" to "Verified" only after CI passes). No milestone starts without approval.
- **Authorization:** every new read path applies the incident visibility rule (`_visible_to`), with cross-reporter negative tests; authorization is enforced in the API, not only the UI.
- **Writes:** every new state-changing endpoint requires authentication and the CSRF token (covered by the automatic route-inventory test); changes to incidents are recorded in the append-only audit trail in the same transaction.
- **Data handling:** no secrets, credentials or real personal data in the repository, fixtures, logs or CI artifacts; user-supplied text is rendered as text; new migrations are reversible and pass `alembic check`.
- **Verification:** deliberate-break checks on new security and correctness rules; dependency audits; no new dependency without justification (pinned, at least two weeks old).

### Milestone 2 — Audit trail · Complete (`7838f9c`, `ec3ed2e`)

Every change to an incident is recorded and cannot be edited.

**Acceptance criteria**
- [x] `incident_events` table (migration) storing incident ID, **authenticated actor** (user ID), **timestamp** (server-side, UTC), **action** (`created`, `updated`, `status_changed`, …), and **before/after values** of each changed field.
- [x] Events are written in the same database transaction as the change, so a change is never saved without its audit record.
- [x] No API exists to edit or delete audit events.
- [x] `GET /incidents/{id}/events` returns the history, newest first, only to users allowed to view the incident (same visibility rule; 404 otherwise).
- [x] Incident detail page shows a readable activity timeline.
- [x] Tests prove: each create and update produces exactly one correct event; no-op updates produce none; actor and diff are correct; history respects permissions, including cross-reporter denial.

### Milestone 3 — Analytics dashboard · Complete (`7240e3a`)

A dashboard of incident metrics computed in PostgreSQL.

**Acceptance criteria**
- [x] Aggregation endpoint(s) computed with SQL aggregates (not by loading all rows into Python): counts by status and severity; open incidents by severity; incidents created per day or week over a selectable range; median time to resolve.
- [x] Results apply the same visibility rule (reporters' figures cover only their own incidents).
- [x] Indexes added where needed, with query plans checked for the main aggregates.
- [x] Frontend dashboard page with accessible charts or tables (text equivalents for charts), loading, empty and error states, and responsive layout.
- [x] Backend tests with seeded data assert exact aggregate values per role; frontend tests cover rendering and states.

### Milestone 4 — Secure CSV export · Complete (`02eba64`)

Export the incidents a user is allowed to see, using the active filters.

**Acceptance criteria**
- [x] `GET /exports/incidents.csv` (approved path, replacing the originally proposed `/incidents/export.csv`) accepts the same `severity` and `status` filters as the list and returns only incidents the caller may view (same visibility rule).
- [x] Authentication required; permissions are identical to the list endpoint.
- [x] **CSV injection prevented**: cells beginning with `=`, `+`, `-`, `@`, tab or carriage return are neutralised (also LF, after leading whitespace, and full-width forms).
- [x] Correct quoting of commas, quotes and newlines; UTF-8; `Content-Disposition: attachment` with a safe filename.
- [x] Streamed output with a documented maximum row count, so a large export cannot exhaust memory.
- [x] Frontend "Export CSV" button on the list that uses the current filters.
- [x] Tests cover filters, permissions (including cross-reporter denial), escaping, injection neutralisation and headers.
- [x] Every export request recorded in an append-only table (approved addition).

### Milestone 5 — Playwright end-to-end tests in CI · Complete (`13d3c34`, `7e65e81`, `dea4ba7`)

Turn the manual browser verification into a permanent, automated suite. Plan approved on 2026-11-15; see *Completed functionality* for the local verification.

**Acceptance criteria**
- [x] `@playwright/test` suite in the repository covering: login per role, create, list with filters and pagination, detail edit and status change, audit timeline, dashboard, CSV export, and permission denials.
- [x] Runs against the full Docker Compose stack with a disposable database seeded by fixtures (admin created with the bootstrap command), never a developer database.
- [x] New CI job that runs the suite and uploads failure evidence; no repository secrets needed. (Approved refinement: screenshots, page snapshots and backend logs only; traces are local-only because they can contain request bodies.)
- [x] Includes mobile-viewport checks for no horizontal scrolling, and a basic automated accessibility scan of the main pages.
- [x] Reliable: no fixed sleeps; passes repeatedly in CI. (Four consecutive full local runs passed; CI run 43726750357 passed. One CI timing failure, run 43724759395, was fixed in `dea4ba7`; keep watching for recurrence.)

### Milestone 5A — Incident comments and internal-only responder notes · Planned

Discussion on an incident: public comments that the reporter can see, and internal notes visible only to responders and admins.

**Anticipated technical additions (not implemented)**
- `incident_comments` table (migration): incident, author (from the session), body (length-limited text), visibility `public` or `internal`, timestamps; edit and delete policy decided in the plan (for example, edits recorded rather than overwritten).
- Endpoints such as `GET/POST /incidents/{id}/comments`, filtered by role in the query itself.
- Comment activity recorded in the audit trail (new event actions or a linked record) with the authenticated actor.
- Detail-page "Discussion" section, with internal notes visibly marked for staff.

**Acceptance criteria**
- [ ] Reporters can read and add public comments on their own incidents only; responders and admins can read and add both kinds on any incident.
- [ ] Internal notes never reach a reporter through any path: comment list, incident detail, history, analytics, CSV export or error messages (negative tests per path, including end-to-end).
- [ ] Author identity always from the session; bodies rendered as text; size limits enforced; CSRF on writes.
- [ ] Comment creation (and any edit or delete allowed by the approved policy) is auditable with actor and time.
- [ ] Backend, frontend and end-to-end tests, including cross-reporter isolation and deliberate-break checks.

### Milestone 5B — SLA tracking, deadlines, overdue indicators and escalation · Planned

Response and resolution targets per severity, visible deadlines, overdue status and escalation.

**Anticipated technical additions (not implemented)**
- SLA policy per severity (response target and resolution target), stored in a table or configuration and changeable only by admins; definitions of "responded" (for example first triage action by staff) and "resolved" reuse the audit trail, as the analytics do.
- Deadlines derived from `created_at` and the policy; overdue and breached states computed in SQL so list filters, dashboard figures and exports agree.
- Escalation when a deadline passes (for example raising an escalation level and notifying or highlighting for staff), recorded in the audit trail. This needs a system-actor design, because audit events currently require a human actor; the approach is decided in the plan.
- Overdue indicators in the list and detail page, an "overdue" filter, SLA compliance figures on the dashboard, and SLA columns in the CSV export.
- A testable clock (injectable "now") so deadline and escalation tests are deterministic.

**Acceptance criteria**
- [ ] SLA targets per severity are documented, configurable by admins only, and changes are auditable.
- [ ] Deadlines, overdue and breached states are correct at boundaries (exactly at the deadline, after a status change, after reopening) and computed in UTC with display in the viewer's time zone.
- [ ] Escalation happens once per breach, is idempotent and is recorded with its cause.
- [ ] Visibility rules apply to every SLA figure, filter and export (cross-reporter tests).
- [ ] Unit, API, end-to-end and deliberate-break tests; performance checked on the benchmark dataset.

### Milestone 5C — AI-assisted incident triage with human approval · Planned

Suggested severity and triage notes for new incidents, which a responder reviews before anything changes.

**Anticipated technical additions (not implemented)**
- A triage service that sends incident text to a language model and requests **structured output** validated against a strict schema (for example suggested severity from the existing enum, a short rationale, suggested next steps, possible duplicate incident IDs from the caller's visible set).
- Suggestions stored separately from the incident, with model identifier, prompt version, timestamps and validation result; nothing is applied automatically.
- A staff-only review panel: accept (applying changes through the normal, audited update path with the human as actor), edit, or reject.
- Provider API key supplied only through a secret store or environment variable, never the repository; the feature disabled when no key is configured; tests use a deterministic fake model.
- A small, synthetic, version-controlled evaluation dataset (for example 30–50 labelled incidents, including prompt-injection attempts) with a script reporting agreement with human labels, invalid-output rate and injection resistance.

**Security and acceptance criteria**
- [ ] Incident text is treated as untrusted input: the model has no tools, database access or ability to act; output outside the schema or allowed values is rejected; prompt-injection cases in the evaluation set do not change behaviour beyond a schema-valid suggestion.
- [ ] Human approval is required for every change; accepted suggestions are recorded in the audit trail with the approving user as actor and a reference to the suggestion.
- [ ] Data minimisation: only fields needed for triage are sent; reporters cannot see staff-only data through suggestions; visibility rules apply to duplicate detection.
- [ ] Timeouts, rate limits and a cost ceiling; failures degrade gracefully to manual triage.
- [ ] Evaluation results recorded in this document; CI uses the fake model only (no secrets or paid calls in CI).

### Milestone 6 — Production hardening, observability, monitoring and deployment · Planned

The original hardening and deployment criteria below are unchanged; observability and monitoring are added. README improvements, screenshots and the architecture diagram move to the *Final presentation*.

**Acceptance criteria**
- [ ] Production frontend image: static build served by a minimal web server that also proxies `/api`; no dev server in production.
- [ ] Containers run as non-root users; minimal base images; health checks; migrations applied automatically and safely at deploy time.
- [ ] Security headers (Content-Security-Policy, HSTS when on HTTPS, `X-Content-Type-Options`, frame protections); CORS locked down; request size limits.
- [ ] Configuration only through environment variables, with a documented `.env.example`; no default credentials in production paths (resolves ISSUE-4).
- [ ] Shared, restart-safe login rate limiting with correct client IPs behind the proxy (ISSUE-7); expired-session cleanup (ISSUE-9); HTTPS-only cookies (ISSUE-11).
- [ ] Least-privilege database role for the app (no ownership; `INSERT`/`SELECT` only on `incident_events`) (ISSUE-12); automatic unassignment on demotion or deactivation, recorded in the audit trail (ISSUE-10).
- [ ] Structured logging without secrets or personal data; consistent error responses.
- [ ] Dependency review and CI dependency/vulnerability scanning; resolve ISSUE-3.
- [ ] Documented deployment to a chosen host, with a deploy workflow that uses a protected environment for any secrets.
- [ ] Final documentation: README with architecture diagram, screenshots, setup, testing, security model and roles, API reference, and a short "design decisions" section. *(Delivered with the Final presentation.)*
- [ ] Observability: structured logs with request IDs (no secrets or personal data), liveness and readiness endpoints, and application metrics (request rate, latency and errors per route; database pool; export and analytics counts).
- [ ] Evaluate **Prometheus** (metrics endpoint and scraping), **Grafana** (provisioned dashboards) and **OpenTelemetry** (traces across FastAPI and SQLAlchemy); record the decision and adopt what fits the chosen hosting, with the reasons documented.
- [ ] Monitoring and alerting for availability, error rate and (after Milestone 5B) SLA breaches, with a short runbook.
- [ ] Re-run the analytics and export benchmarks at production-like volume (ISSUE-13) and decide on read replicas or caching (ISSUE-14).

### Final presentation · Planned

A short, verifiable showcase of the finished platform.

**Acceptance criteria**
- [ ] Live demo deployment with **synthetic sample data** generated by a committed script (no real data); demo accounts per role with a published reset policy; no admin credentials published.
- [ ] Screenshots of the main screens (light and dark, desktop and mobile) and an architecture diagram kept in the repository.
- [ ] README improvements: overview, architecture, security model and roles, setup, testing, API reference, design decisions and links to CI and this specification.
- [ ] A 60–90 second recorded walkthrough (script or storyboard committed), with captions, showing sign-in, triage, history, dashboard and export, using only synthetic data.

---

## Definition of done (every milestone)

- All existing backend, frontend and (once added) end-to-end tests pass locally and in GitHub Actions.
- New behaviour has tests, including failure and permission paths.
- No secrets, generated artifacts or unrelated files are committed.
- Documentation, including this file's status tables, is updated in the same milestone.
- The milestone followed the approval gates above; evidence is marked "Verified" only after CI passes.
