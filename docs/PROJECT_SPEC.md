# Project Specification — Security Incident Management Platform

Last updated: 2026-11-15 · Milestone 1 (authentication and RBAC) complete — commit `1ae693b`, CI run [43668840665](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43668840665) passed · Milestone 2 (audit trail) complete — commits `7838f9c` + `ec3ed2e`, CI run [43672524737](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43672524737) passed · Milestone 3 (analytics dashboard) complete — commit `7240e3a`, CI run [43714418907](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43714418907) passed · Milestone 4 (secure CSV export) complete — commit `02eba64`, CI run [43717432124](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43717432124) passed · Milestone 5 (end-to-end tests) complete — commits `13d3c34`, `7e65e81`, `dea4ba7`, CI run [43726750357](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43726750357) passed (all five jobs) · Milestone 5B (SLA tracking and escalation) complete — commit `d0ea3e8`, CI run [43766899182](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43766899182) passed (all five jobs) · Milestone 5A (comments and internal notes) complete — commit `ddc571e`, CI run [43749980036](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43749980036) passed (all five jobs) · Milestone 5C (AI-assisted triage) complete — commit `4a216d7`, CI run [43784327150](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43784327150) passed (all five jobs) · Milestone 5D (dashboard redesign) complete — commit `e3bc126`, CI run [43871416066](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43871416066) passed (all five jobs) · Milestone 6 Phase A (production images, reverse proxy, readiness) complete — commit `5e4548e`, CI run [43890599391](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43890599391) passed (all five jobs) · Milestone 6 Phase B (least privilege, shared rate limits, redaction) complete — commit `8ea9c72`, CI run [43906767771](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43906767771) passed (all five jobs) · Roadmap expanded on 2026-11-15 (Milestones 5, 5A, 5B, 5C, 6 and a final presentation, all **Planned**)

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
| Read public comments | Own incidents only | All, including legacy | All, including legacy |
| Read internal notes | ✗ (never returned or counted) | All | All |
| Post public comments | Own incidents only | All | All |
| Post internal notes | ✗ (403) | All | All |
| Edit or delete comments | ✗ (no endpoint) | ✗ (no endpoint) | ✗ (no endpoint) |
| View SLA targets, states and deadlines | Own incidents only | All | All |
| View escalation details and the escalated filter | ✗ (never returned; filter 403) | All | All |
| Read the current SLA policy | ✓ | ✓ | ✓ |
| Create SLA policy versions, view version history | ✗ (403) | ✗ (403) | ✓ (reason required) |

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

### Dashboard and theme design (Milestone 5D)

Approved 2026-11-16 (plan with decisions: donut with an exact-count legend, a manual theme toggle, "SLA overdue now" as the fifth figure, the blue-gray background on every page). Frontend only: no backend, API, migration or dependency changes; every figure comes from `GET /analytics/summary`.

| Topic | Decision | Notes and limits |
|---|---|---|
| Headline figures | Five cards: incidents in range, open backlog now, resolved or closed, median time to resolve, SLA overdue now. Each says in text whether it covers the **selected dates** or is **right now**, and carries a blue, orange, green, purple or red accent with a decorative icon. | Reporters see their own incidents only (server scope, unchanged). |
| Charts | Created-versus-resolved trend (unchanged component: keyboard readout, tooltip, table view); status donut; severity bars; SLA panel with on-time rates, overdue and approaching counts and staff-only escalations; current backlog and resolution times. | No chart library: hand-written SVG and HTML. |
| Status donut | Four segments with surface gaps; the legend beside it always lists each status with its exact count and share; the ring is focusable and arrow keys read each segment (centre readout and a polite status message); a summary label gives every value. | Colours are reference-palette slots (open blue, investigating orange, resolved aqua, closed yellow), ordered so every neighbouring pair including the wrap passes the colour-vision checks (worst ΔE 8.4) and 3:1 contrast in both themes. The badge tones were rejected: blue and purple are 2.0 apart for deuteranopia. |
| Severity bars | Named rows with count and share; bars use the severity badge tones, so a severity looks the same everywhere. | Every bar is at least 3:1 against the card and its track in both themes. |
| Theme | `light-dark()` colour tokens with `color-scheme: light dark`: the operating-system theme applies until the viewer presses **Dark theme** in the header (a toggle button with its state and a description of where the choice comes from). The choice is stored in this browser (`localStorage`, key `imp-theme`) and applied as `data-theme` on `<html>` before the first render. | Storage failures are tolerated (the choice applies to the page). No inline script, so a future Content Security Policy needs no exception. |
| Layout | Five cards in a row at 1,280 px; 3 + 2 at 1,024 px; 2 columns at 768 px and 390 px, with the fifth card full width. Trend beside the donut; severity and current backlog stacked beside the SLA panel; resolution times full width. Page container widened from 1,120 to 1,240 px so the header fits on one row. | No horizontal scrolling at 390, 768, 1,024 or 1,280 px (end-to-end checks). |
| Light background | `#eef2f7` on every page in the light theme; muted text stays 5.3:1 on it. | Dark theme unchanged in its values, now also selectable explicitly. |

### Production deployment design (Milestone 6)

Approved 2026-11-16: a public portfolio demo on one Oracle Cloud Always Free ARM server (subject to capacity), Docker Compose, Caddy with a free DuckDNS hostname, synthetic data only, simulated AI triage (`TRIAGE_PROVIDER=fake`), no Pay As You Go upgrade. Delivered in phases A–G; **Phase A** (production images, reverse proxy, readiness) is complete and CI verified (`5e4548e`); the Phase B and Phase C rows are marked below. Nothing is deployed yet.

| Topic | Decision (Phase A) | Notes and limits |
|---|---|---|
| Stack | `deploy/compose.prod.yaml`: `db` (PostgreSQL 17, data volume, no published ports) → `migrate` (`alembic upgrade head`, must succeed) → `backend` and `worker` → `web` (Caddy, the only published ports). The development `compose.yaml` is unchanged. | Start-up order is enforced with health and completion conditions. |
| Images | `backend/Dockerfile --target prod`: runtime dependencies only, installed from `backend/requirements-runtime.lock` (39 packages, hash-pinned, amd64 and arm64), user 10001, liveness health check. `deploy/web.Dockerfile`: the React build served by Caddy as user 10002. The default (development) backend image is unchanged in behaviour. | Verified on arm64 under emulation before Phase A: backend 510 tests, worker, identical frontend build. |
| Reverse proxy | Caddy: automatic HTTPS for `SITE_ADDRESS`, HTTP→HTTPS redirect, `/api/*` to the backend with the prefix removed and a 1 MB body limit, single-page-app fallback, immutable caching for hashed assets and revalidation for pages, admin API off. | Same origin, so no CORS. |
| Security headers | CSP `default-src 'self'` with no inline scripts or styles, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'`; HSTS one year (this host only, because DuckDNS is a shared domain); nosniff; `X-Frame-Options: DENY`; `Referrer-Policy: no-referrer`; Permissions-Policy; COOP and CORP; no `Server` header. | Session cookies were already Secure, HttpOnly and SameSite=Strict. |
| Client address | Caddy replaces any client-supplied `X-Forwarded-For`; uvicorn trusts forwarded addresses only from Caddy's fixed internal address (`FORWARDED_ALLOW_IPS`). | Real client addresses must be confirmed on the Linux server (Phase F). |
| Readiness | `/health` liveness (no database); `/health/db` (shared engine; ISSUE-4 fixed); `/health/ready` returns 503 with `migrations_pending`, `unknown_database_revision` or `database_unavailable` unless the database revision equals the code's migration head. Revisions are logged, never published. The SLA worker skips its runs until ready. | Prevents a repeat of the local 0005-versus-0008 dashboard outage. |
| Production settings | `APP_ENV=production` hides the API docs (`/docs`, `/redoc`, `/openapi.json`) and refuses to start when cookies are not Secure, `DATABASE_URL` is missing, uses a development password or a password under 24 characters, `TRIAGE_PROVIDER=anthropic` or any `ANTHROPIC_API_KEY` is set, or `FORWARDED_ALLOW_IPS` is empty or `*`. Messages never include secret values. | `POSTGRES_PASSWORD` and `SITE_ADDRESS` are required by the compose file; no defaults. |
| Database roles (Phase B) | The schema owner (`POSTGRES_USER`) only runs `alembic upgrade head` and `python -m app.cli db-roles` in the `migrate` service. The API and worker connect as `imp_app` (`APP_DB_USER`/`APP_DB_PASSWORD`), which owns nothing: audit tables SELECT and INSERT only; `users` and `incidents` no DELETE; `auth_sessions` and `rate_limit_events` DELETE for housekeeping; `incident_escalations` MAINTAIN for `ANALYZE`; no TRUNCATE, no CREATE. Every table must be classified in `app/db_roles.py`, or `db-roles` refuses to run. | In production `/health/ready` returns 503 `unsafe_database_role` if the app connects as a superuser, a table owner or a role that can modify audit tables. The owner is still the image's superuser. |
| Rate limits (Phase B) | `rate_limit_events` (PostgreSQL) replaces the in-memory limiter: failed sign-ins per email (5) and per client IP (30) in 15 minutes, shared by every process and kept across restarts; keys stored only as SHA-256 hashes; attempts on one key serialised by a transaction-level advisory lock; a failure is committed even though the request fails. Public-demo writes per client IP per hour: incidents 20, comments 60, triage requests 20 (`WRITE_LIMIT_*_PER_HOUR`; off by default outside the production compose file). | Cleanup keeps events for the longest window in use, so it can never lift an active lockout. Shared networks share per-IP limits. |
| Input validation (Phase B) | Incident title and description, user-creation email and sign-in email reject NUL, other control characters and lone surrogates with 422 (BUG-2). Tab, line feed and carriage return stay allowed in incident text. | API-level only; no database constraint, so existing rows are not affected. |
| Comment redaction (Phase B) | `POST /incidents/{id}/comments/{comment_id}/redaction` (admin, CSRF, reason 1–500 characters): one append-only `comment_redactions` row (who, when, why). Every comment response is built by one function that withholds the body once redacted; the reason is shown to staff only. The comment row is unchanged evidence. | Irreversible. The original text stays in the database and backups (retention: ISSUE-23). Incident text cannot be redacted. |
| Housekeeping (Phase B) | `python -m app.cli cleanup`, run hourly by the worker: expired and idle sessions, old rate-limit events. | — |
| Public demo (Phase C) | `DEMO_MODE=true` publishes two accounts on the sign-in page and at public `GET /demo`: `demo.reporter@example.com` and `demo.responder@example.com` (passwords shown on purpose). The administrator stays private. A banner on every page says the data is synthetic, AI triage is simulated and the demo resets daily at 04:00 UTC. Demo accounts: failed sign-ins never lock them out by email (the per-IP limit still applies, so a visitor can only lock out their own address); password change 403; an administrator cannot change their role or deactivate them (403). In demo mode the paid AI provider is refused even if configured. Outside demo mode none of this applies. | Shared accounts: visitors see each other's changes until the next reset (ISSUE-31). |
| Daily demo reset (Phase C) | `demo-reset` service (`--profile demo`, schema owner): seeds a fresh demo database at start, then `python -m app.cli demo-reset --daily` at 04:00 UTC. An administrator resets now with `docker compose -f deploy/compose.prod.yaml --profile demo run --rm demo-reset python -m app.cli demo-reset` (exit 0 done, 2 refused, 1 failed and rolled back). Refuses unless `DEMO_MODE` is on, `DEMO_DATABASE` equals the connected database and its name ends in `_demo`. One transaction: empty every table except `users` and `alembic_version`, keep administrators, recreate the default SLA policy, the demo accounts and 15 back-dated scenarios (6 comments, 3 offline-provider suggestions) through the application's own audit and SLA code, and check SLA consistency before committing. Takes the SLA worker lock (the worker skips runs meanwhile); a 30-second lock timeout stops a stuck transaction from freezing the API; a deadlock with a request is retried up to 3 times. All sessions end at the reset. | A request caught in a deadlock with the reset can fail once (ISSUE-32). |
| Container hardening | Backend and worker: read-only root file system, all capabilities dropped, no new privileges. Web: only `NET_BIND_SERVICE`. | Base images still use version tags; digest pinning is Phase E. |

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

### Comments and internal notes design (Milestone 5A)

`GET` and `POST /incidents/{incident_id}/comments` (`backend/app/comments.py`). Approved decisions D1–D11 (2026-11-15).

| Topic | Decision | Notes and limits |
|---|---|---|
| Incident authorization | Both endpoints call the existing `_get_visible` **first**, so a reporter gets the same 404 body for someone else's incident, a legacy incident or a missing one, whatever they send (including a request for an internal note). | Tested for GET and POST, with paging parameters, in the API and end to end. |
| Separation | `_comments_visible_to` adds `visibility = 'public'` for reporters **inside the SQL**, for both the rows and `total`, so the count never reveals internal notes. Responders and admins read both kinds. A reporter posting `internal` gets **403** ("Reporters can only post public comments."), after the visibility check. | `visibility` is required and has no default in the API or the database. |
| Fail closed | Before a reporter's list response is built, any non-public row raises a 500 (logged with the user ID only, never the body) instead of being sent. | Proven by a test that disables the SQL filter. |
| Other paths | Incident detail and list, history (`incident_events`), analytics, CSV export and `/auth/me` never read comments. Adding a comment **does not touch the incident row**, so `updated_at` cannot reveal internal activity. | A canary test checks every reporter-readable endpoint and error response; analytics and export output are byte-identical before and after comments are added; the incident row's physical version (`ctid`) is unchanged. |
| Audit record (D2) | The append-only `incident_comments` row is the comment's audit record: author (from the session), visibility, body and database time. Comment activity is **not** written to `incident_events`, so the history endpoint is unchanged. | No `incident_event_action` enum change, which keeps migration `0006` cleanly reversible. |
| Immutability (D1) | No edit or delete endpoints (405). A trigger rejects `UPDATE`, `DELETE` and `TRUNCATE`; both foreign keys are `RESTRICT`. | Same owner-can-disable limit as the other append-only tables (ISSUE-12). Content published by mistake cannot be removed through the app: ISSUE-18, a production-readiness blocker. |
| Body rules (D5) | Line endings become LF; a fixed set of blank characters is trimmed from both ends (space, tab, LF, U+00A0, U+1680, U+2000–U+200B, U+2028, U+2029, U+202F, U+205F, U+3000, U+FEFF); then 1–**5,000** code points; no C0 controls other than tab and LF, no DEL or C1 controls (U+007F–U+009F), no NUL and no lone surrogates. | Length counts Unicode code points (an emoji counts once), as PostgreSQL's `char_length` does. A raw input above 20,000 characters is refused before processing. |
| API and database agree | The same rules are three check constraints (`ck_incident_comments_body_length`, `_chars`, `_trimmed`). Tests insert 40 edge-case bodies directly and evaluate about 50,000 generated bodies (every code point up to U+30FF and U+FE00–U+FF0F, alone, leading, trailing and inside text) against the live constraint definitions: PostgreSQL accepts **exactly** the bodies the API would store unchanged. | NUL and lone surrogates cannot reach PostgreSQL text at all; the API returns 422 for both (not 500). |
| Ordering and paging (D4) | Oldest first by (`created_at`, `id`); `limit` 1–100 (default 50), `offset` ≥ 0. Because comments are append-only, earlier pages never shift; the UI still removes duplicates by ID. | — |
| Rendering | Bodies are React text with `white-space: pre-wrap`: no HTML, Markdown or automatic links. | Verified with HTML/JavaScript payloads in unit and end-to-end tests. |
| Frontend | "Discussion" section on the detail page above History. Internal notes carry the text "Internal note" and "Not visible to the reporter" and a tinted border (`--internal` tone, contrast-checked on that background). Staff choose visibility with a labelled radio group that **defaults to Internal note** (D3); the submit button names the choice ("Add internal note" / "Post public reply") and a public reply shows "The reporter will see this reply." (linked to the textarea). Reporters have no visibility control. Character counter in code points, a polite announcement only near the limit, field errors linked with `aria-describedby`, "Comment posted." / "Internal note added." announced, focus returned to the textarea. | A "Reporter" badge is derived in the browser from the incident's reporter (D8); no role data is added to the API. |
| Out of scope | No visibility filter parameter (D9); comments are not in the CSV export or dashboard (D10); no rate limit on posting (D11, ISSUE-19); comments are allowed on every status (D6). | — |

### SLA design (Milestone 5B)

Approved decisions D1–D14 (2026-11-15) with refinements 1–10, and the performance refinement (stored read model, batched worker).

| Topic | Decision | Notes and limits |
|---|---|---|
| Targets (D1) | Per severity, in minutes: critical 15 / 240, high 60 / 1,440, medium 240 / 4,320, low 1,440 / 10,080 (response / resolution). Calendar time, 24×7 (D9). | Response 1–43,200 min, resolution 1–525,600 min, response ≤ resolution, and a more severe incident never has a longer target than a less severe one (API and database). |
| Acknowledgment (D2) | The first audited event that moves the status away from `open` or sets an assignee (both staff-only). A staff-created incident with an assignee is acknowledged at creation. Comments and internal notes never count; acknowledgment is never reversed. | — |
| Cycles (D3) | Cycle 0 starts at creation; every reopen (resolved/closed → open/investigating) starts the next cycle with a new resolution target. The response target exists in cycle 0 only. Earlier cycles keep their final met/breached outcome. | The dashboard reports first-cycle compliance; every cycle is on the detail page and in `GET /incidents/{id}/sla`. |
| Severity and policy (D5, D8) | The policy version is frozen at cycle start (latest version with `created_at` ≤ the start; ties: highest id). Targets use the strictest severity held from the cycle start until the target is satisfied; deadlines are measured from the original start, so a severity change never resets the clock or relaxes a target. A satisfying event's own severity change does not count for that target. | Incident writes take a shared advisory lock and policy changes an exclusive one, with `created_at` from the database clock, so a new version is always later than any cycle start already being committed. |
| States (D4) | `on_track`, `approaching` (from 75% of the target), `overdue` (at or after the deadline), `met` (satisfied at or before the deadline), `breached` (satisfied after it), `not_tracked`. The list shows the most urgent current target. | Badges always carry the state as text. |
| Clock (D11) | The database clock (`clock_timestamp()`) through an injectable `get_now` dependency; responses include `now` and the browser computes relative times from it plus monotonic time, never from `Date.now()`. | Tests freeze the clock; no production code path accepts a clock override. |
| Not tracked (D10) | Legacy incidents (no `created` event) and cycles that started before the first policy version. | So deploying `0007` creates no escalation flood. |
| Stored read model | `incident_sla_cycles`: one row per cycle, maintained by an incremental algorithm (`app/sla_store.py`) in the same transaction as each incident change, under the incident row lock. `incident_events` remains the source of truth; `app/sla.py` derives the same values from it independently. | A trigger keeps history final: no deletes, a completed cycle never changes, start and policy version never change, an acknowledged response is final, and deadlines can only become stricter. |
| Consistency | Every SLA scenario test ends by comparing the stored cycles with the audit-derived reference (`sla_store.verify`); `python -m app.cli sla-verify` does the same for a whole database (used by `e2e/run.sh` before and after the suite). | Migration 0007 backfills existing incidents with the same definitions (`BACKFILL_SQL`). |
| Escalation (D6, D7, D12) | Append-only `incident_escalations` rows, `triggered_by = 'system'` (checked), unique per incident, cycle and target. The `sla-worker` service runs `python -m app.cli sla-escalate --loop` (60 s). A bounded run takes the worker advisory lock, reads up to 500 overdue targets from partial indexes, and processes them in batches of 100: lock the incident rows in id order, recheck and insert in one statement with `ON CONFLICT DO NOTHING`. | Satisfied targets are never escalated retrospectively. In-app indicators only (escalated badge, staff filter, dashboard count); email or webhook notification is deferred (ISSUE-21). After a full run the worker refreshes `incident_escalations` statistics (see benchmark). |
| Visibility (D14) | Every SLA query is restricted with `_visible_to`; `GET /incidents/{id}/sla` uses `_get_visible` (same 404). Escalation details, the escalated flag and the escalated filter are staff-only. Reporters see their own incidents' targets and states. Nothing in the SLA code reads comments. | Canary tests prove internal notes never appear in SLA responses, lists, dashboards or exports. |
| Export (D14) | Four columns: `response_due_utc`, `response_state` (first cycle), `resolution_due_utc`, `resolution_state` (current cycle). | The list's SLA filter does not apply to the export (ISSUE-20). |

### AI triage design (Milestone 5C)

Approved plan with decision T1 (cheapest current Haiku, no automatic upgrade) and the implementation conditions of 2026-11-15.

| Topic | Decision | Notes and limits |
|---|---|---|
| Scope | Responders and admins can request a suggestion: severity (existing enum), a rationale (1–600 characters), up to five next steps (1–200 characters each) and possible duplicates chosen from up to five candidates. | Reporters get 403 on every triage endpoint and never see a suggestion, decision or history marker. |
| Human review | A suggestion never changes the incident. Accepting applies the chosen (optionally edited) severity through `incidents.apply_incident_changes`, the same audited path as `PATCH /incidents/{id}`, with the deciding person as actor; an optional internal note is added through the comment service. Rejecting changes nothing. One decision per suggestion. | A suggestion is **stale** once any audit event follows the one it was based on (`basis_event_id`); stale suggestions cannot be accepted (409), only rejected. |
| Providers | `off` (default), `fake` (deterministic keyword rules, offline, labelled "Test provider (not AI)") and `anthropic` (official SDK `anthropic` 2.8, model `claude-haiku-5-5` by default). The model is configurable (`TRIAGE_MODEL`); any model other than the default also needs explicit prices, otherwise triage stays off. There is no fallback to another model. | Haiku 5.5 was verified on 2026-11-15: $0.10 / $0.50 per million input / output tokens, structured outputs supported, retirement not before 2027-10-07. |
| Request | One Messages call: system prompt (`triage-v1`), one user message holding the incident as JSON marked untrusted, a strict JSON schema (`output_config.format`), effort `low`, thinking off, no tools, `max_tokens` 1,200, timeout 20 s, one SDK retry. | The schema has no length keywords (the API does not accept them); lengths and counts are enforced by the validator. |
| Data sent | Title, description, severity, status, and up to five other incident titles the requester can see (PostgreSQL full-text search over title and description: the stored generated column `incidents.search_vector`, index `ix_incidents_search`). Never comments, internal notes, people, email addresses or SLA data. | **Synthetic data only** until a production retention and redaction policy exists (ISSUE-23). |
| Validation | Strict, never repaired: exactly the four keys; severity from the enum; text trimmed and whitespace collapsed, within limits, no control characters, URLs, web or email addresses; duplicate ids unique integers from the candidates sent. Anything else is stored as `invalid_output` with a category only. | Raw model output is never stored or logged. |
| Budgets | Worst-case reservation per call (one token per UTF-8 byte of input plus 400, plus the full output allowance), in micro-dollars. Spent = actual cost of finished calls plus the full reservation of unfinished ones. Limits: cooldown 60 s per incident, 10 requests per user per hour, $0.01 per incident, $0.05 per user per day, 300 requests and $0.50 per day overall, $5.00 per month overall; 429 with `Retry-After`. | Calls whose billing is unknown (timeouts, lost connections, unreported usage) or that returned no usage are charged their full reservation. |
| Concurrency | Reservation: one short transaction under advisory lock `51_000_003` that closes stale reservations, checks every limit and inserts the request, then commits. The provider is called with **no transaction open and no row lock**. The result is recorded in a second short transaction. A decision locks the incident row briefly (no provider call). | Reservations without a result after 600 s are closed as `abandoned` at full cost; a late result keeps the abandoned record. Budgets can only be over-counted. |
| Audit and storage | Append-only `triage_requests` (who, when, provider, model, prompt version, input SHA-256, candidates, basis event, reservation), `triage_suggestions` (outcome, validated fields, tokens, cost, latency) and `triage_decisions` (who, decision, applied severity, resulting audit event, note, reason). Staff history shows "Applied from triage suggestion #N after review". | Logs contain identifiers, outcome, tokens and cost only. |
| Interface | Staff-only "Triage assistant" card on the detail page: source label in text ("AI-generated suggestion" or "Test suggestion (not AI)"), model and prompt version, a "What is sent" disclosure, remaining allowance, plain-text rendering of all model output, editable severity, optional editable internal note with the comment rules, reject with reason, focus moved to the result and to the decision. | — |

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

**`incident_comments`** (migration `0006`): append-only discussion entries.

| Column | Type | Rules |
|---|---|---|
| `id` | integer, primary key | ordering tie-breaker |
| `incident_id` | integer, FK → `incidents` (`RESTRICT`) | |
| `author_id` | integer, FK → `users` (`RESTRICT`), not null | the signed-in user; never client input |
| `visibility` | enum `comment_visibility` | `public`, `internal`; **no default** |
| `body` | text | check constraints: 1–5,000 characters, no forbidden control characters, no leading or trailing blank characters (see *Comments and internal notes design*) |
| `created_at` | timestamptz | set by the database at insert |

Indexes `ix_incident_comments_incident_id_visibility_id (incident_id, visibility, id)` and `ix_incident_comments_author_id`. A trigger rejects `UPDATE`, `DELETE`, `TRUNCATE`. There is no `updated_at`: comments never change.

**`sla_policy_versions`** (migration `0007`): `id`, `source` (enum `sla_policy_source`: `default`, `admin`), `created_by` (FK → `users`, `RESTRICT`; required for `admin`), `reason` (1–500 characters), `created_at` (the time from which the version applies). Append-only (trigger). Version 1 is the seeded default.

**`sla_policy_targets`** (migration `0007`): primary key (`version_id` FK → `sla_policy_versions`, `severity`), `response_minutes`, `resolution_minutes` (range and order checks). Append-only.

**`incident_sla_cycles`** (migration `0007`): stored SLA read model. Primary key (`incident_id` FK → `incidents` `RESTRICT`, `cycle`); `started_at`; `policy_version_id` (null = not tracked); `response_rank`, `response_minutes`, `response_due_at`, `response_warning_at`, `acknowledged_at` (cycle 0 only); `resolution_rank`, `resolution_minutes`, `resolution_due_at`, `resolution_warning_at`, `resolved_at`; generated `response_breached` and `resolution_breached`. Partial indexes on outstanding response and resolution deadlines and on breached cycles. Guard trigger as described in *SLA design*.

**`incident_escalations`** (migration `0007`): `id`, `incident_id` (FK `RESTRICT`), `cycle`, `target` (enum `sla_target`), `deadline_at`, `policy_version_id` (FK), `triggered_by` (always `system`), `created_at`; unique (`incident_id`, `cycle`, `target`); a response escalation only in cycle 0. Append-only.

**`triage_requests`** (migration `0008`): `id`, `incident_id` (FK `RESTRICT`), `requested_by` (FK → `users`, `RESTRICT`), `provider`, `model`, `prompt_version`, `basis_event_id` (FK → `incident_events`, nullable), `input_sha256`, `candidate_ids` (JSONB), `reserved_microusd` (≥ 0), `created_at`. Indexes on (`requested_by`, `created_at`), (`incident_id`, `created_at`) and `created_at`. Append-only.

**`triage_suggestions`** (migration `0008`): `id`, `request_id` (unique FK), `outcome` (enum `triage_outcome`: `valid`, `invalid_output`, `refused`, `timeout`, `provider_error`, `abandoned`), `error_category`, `severity`, `rationale` (1–600), `next_steps` and `duplicate_ids` (JSONB), `input_tokens`, `output_tokens`, `cost_microusd` (≥ 0), `latency_ms`, `created_at`. A check requires the suggestion fields exactly when the outcome is `valid`. Append-only.

**`triage_decisions`** (migration `0008`): `id`, `suggestion_id` (unique FK), `decided_by` (FK → `users`), `decision` (enum `triage_decision_kind`: `accepted`, `accepted_with_edits`, `rejected`), `applied_severity`, `resulting_event_id` (unique FK → `incident_events`), `note_comment_id` (unique FK → `incident_comments`), `reason` (1–500), `created_at`. A check keeps a rejection free of applied changes. Append-only.

**`incidents.search_vector`** (migration `0008`): a stored generated column, `to_tsvector('english', title || ' ' || description)`, computed by PostgreSQL on every insert and update (it cannot be written directly), with the GIN index `ix_incidents_search`. Deferred in the ORM, so incident queries do not load it, and never part of an API response or export. Adding it rewrites `incidents` once (5.3 s at 52,000 rows).

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
| `GET` | `/incidents/{id}/comments` | Any role (visibility applies) | Discussion oldest first. `limit` 1–100 (default 50), `offset` ≥ 0. Returns `{items, total, limit, offset}`; each item is `{id, visibility, body, author: {id, email}, created_at}`. Reporters receive, and `total` counts, public comments only. 404 when the incident is missing or not visible. |
| `POST` | `/incidents/{id}/comments` | Any role (visibility applies) | Body `{body, visibility}`, both required; unknown fields (such as `author_id`) 422. Reporters: `public` only (403 for `internal`). Body rules in *Comments and internal notes design*. 201 with the comment. No edit or delete routes (405). |
| `GET` | `/incidents/{id}/sla` | Any role (visibility applies) | `{now, tracked, current_cycle, cycles, escalations}`; each cycle has `response` (cycle 0) and `resolution` with target minutes, effective severity, due and warning times, satisfaction time and state. `escalations` is null for reporters. 404 like the incident. |
| `GET` | `/incidents?sla=overdue\|approaching\|breached\|escalated` | Any role (visibility applies) | SLA filter on the list; `escalated` is staff-only (403). Every list item carries `sla: {state, target, due_at}` or null, and `escalated` (null for reporters). |
| `GET` | `/sla/policy` | Signed in | The current policy version and its targets. |
| `GET` | `/sla/policy/versions` | Admin | Every version, newest first, with author and reason. |
| `POST` | `/sla/policy/versions` | Admin | `{reason, targets: [4 × {severity, response_minutes, resolution_minutes}]}` (strict integers); 201. Applies to cycles that start afterwards. |
| `GET` | `/exports/incidents.csv` | Any role (visibility applies) | Optional `severity`, `status`. Streams the CSV described in *CSV export design*. 413 above `EXPORT_MAX_ROWS` (default 10,000) with `X-Export-Row-Count`; 422 for invalid filters. Each request is recorded in `incident_exports`. Read-only (405 for writes). |
| `GET` | `/analytics/summary` | Any role (visibility applies) | `from`, `to` (inclusive dates), `tz` (IANA). Returns `{scope, range, totals, by_status, by_severity, open_backlog_by_severity, trend, resolution}`; see *Analytics design*. 422 for invalid ranges or time zones. Read-only (405 for writes). |
| `GET` | `/triage/status` | Responder, admin | `{enabled, reason, provider, provider_label, is_ai, model, prompt_version, sent_fields, typical_cost_usd, limits}`. |
| `POST` | `/incidents/{id}/triage-suggestions` | Responder, admin | Request a suggestion; 201 with it (any outcome). Never changes the incident. 404 like the incident; 503 when off; 429 with `Retry-After` at any limit. |
| `GET` | `/incidents/{id}/triage-suggestions` | Responder, admin | Newest 20, each with `outcome`, `message`, validated fields, `duplicates`, `candidates_sent`, provider, model, prompt version, requester, cost, `stale` and `decision`. |
| `POST` | `/triage-suggestions/{id}/decision` | Responder, admin | `{decision: accept\|reject, severity?, add_internal_note?, note?, reason?}` (unknown fields 422). Accept applies the severity through the audited update; 409 when stale, not valid, or already decided; 422 for an invalid note. |
| `GET` | `/users/assignees` | Responder, admin | Active responders and admins as `{id, email}` |
| `GET` | `/users` | Admin | All accounts |
| `POST` | `/users` | Admin | Create `{email, password, role}`. 409 on duplicate email. |
| `PATCH` | `/users/{id}` | Admin | Change `role` and/or `is_active`. 409 when targeting yourself. Revokes the user's sessions when either changes. |

Incident responses include `reporter_id`, `assignee_id`, and `reporter` / `assignee` as `{id, email}` or `null`.

---

## Completed functionality

### Milestone 6 Phase C — Public demo mode

Implemented on 2026-11-16 (commit pending). Nothing is deployed. No new migrations.

| Functionality | Status | Evidence |
|---|---|---|
| Demo information: off by default (`GET /demo` reports disabled, no accounts, no banner); in demo mode both accounts and the reset time, the banner on every page, and the accounts on the sign-in page with "use this account" buttons | **Verified (local)** | `backend/tests/test_demo.py`; `frontend/src/demo.test.tsx`; `e2e/tests/demo.spec.ts` |
| Demo accounts at the API layer: password change 403; role change and deactivation by an administrator 403; ordinary accounts unaffected; **50 failed sign-ins from 10 addresses** (each below its per-IP limit) never lock out the demo reporter, while an ordinary account is still locked after 5 failures from different addresses | **Verified (local)** | `test_demo.py`; `demo.spec.ts` |
| Reset guards: refused without `DEMO_MODE`, without `DEMO_DATABASE`, on a mismatched name and on a name without `_demo`, so the development, test and end-to-end databases never qualify; refused runs change nothing | **Verified (local)** | `test_demo.py` (a temporary `*_demo` database) |
| Reset result: repeatable (identical fingerprint and user ids on every run), visitors' accounts and data removed, administrators kept, SLA cycles consistent, append-only triggers active again afterwards; an inconsistent seed rolls back and keeps the previous demo | **Verified (local)** | `test_demo.py` |
| Reset now (administrator): exit 0 on the demo database, 2 on the test database, 1 with the previous demo intact when the reset fails | **Verified (local)** | `test_demo.py` |
| Concurrency: a reset waits for an in-flight write and then replaces it; a stuck transaction makes it give up after the lock timeout, the previous demo stays and the API keeps working; a deadlock with a request is retried; active sessions end at the reset (401) and signing in again works; the reset waits for a worker run in progress and a worker run during a reset is skipped; repeated resets with the worker running in a loop produce no errors and consistent SLA data | **Verified (local)** | `test_demo.py` (real transactions on the demo database) |
| No paid AI in demo mode: `build_provider` refuses the paid provider and never constructs its client, even with a key set; production start-up also refuses the paid provider or any key (Phase A); the demo end-to-end stack runs the offline provider with no key | **Verified (local)** | `test_demo.py`; `e2e/compose.e2e-demo.yaml` |
| Public demo end to end: production images, `DEMO_MODE`, the API and worker as `imp_app`, data from the real reset; reporter isolation, responder triage with SLA states and simulated AI, dashboard, lockout and administrator protections, axe checks | **Verified (local)** | `bash e2e/run.sh --demo` (new step in the End-to-end tests job) |

**Results (local, 2026-11-16):** backend **648 passed** (616 before; 32 new) in the CI-equivalent sequence, migration round trip clean; frontend **179 passed** (174 before; 5 new), build clean; end to end **70 passed** (the 4 public-demo tests skipped) on the development images and **4/4** in the public-demo run. Docker volumes and the development database unchanged.

**Deliberate-break checks** (scratch copies; each disabled one protection): backend 16 — lockout exemption removed, per-IP limit removed for demo accounts, password change allowed, administrator changes allowed, demo information published when off, reset without demo mode, without the name match, without the `_demo` suffix, visitors' accounts kept, user ids not deterministic, SLA check skipped, worker lock not taken, no lock timeout, no deadlock retry, paid AI allowed in demo mode, failed reset-now reported as success: **16 caught**. The worker-lock variant first survived (the deadlock retry hid it) and the lock-timeout variant first hung instead of failing; direct tests were added for both. Frontend 2: **2 caught**.

### Milestone 6 Phase B — Least-privilege database role, shared rate limits and comment redaction

Released on 2026-11-16 as `8ea9c7267f6b4c5e73e5123f768a59b00464e3b9` (`feat: add least-privilege database role, shared rate limits and comment redaction`, 36 files). Nothing is deployed. New migrations: `0009` (`rate_limit_events`) and `0010` (`comment_redactions`, append-only).

**CI record:** run [43906767771](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43906767771) on `8ea9c72`: **success** on the first attempt, all five jobs: Backend tests (121 s), Migration round trip (34 s, including `0009` and `0010`), Frontend tests and build (28 s), Docker images (42 s), End-to-end tests (198 s; **70 passed** in 1.2 min of test time, from the job's annotation). The backend (616) and frontend (174) totals are from local runs of the identical tree, because job logs cannot be downloaded without repository admin rights. The fresh-installation check, the production-image end-to-end run as `imp_app`, the migration-with-data check and the deliberate-break checks are local.

| Functionality | Status | Evidence |
|---|---|---|
| Least privilege, against real PostgreSQL roles: `imp_app` refused UPDATE, DELETE and TRUNCATE on every audit table; disabling, dropping or replacing triggers and functions; dropping tables or constraints; creating objects; `session_replication_role`; becoming superuser; reading `pg_authid`; deleting incidents or users. It can do everything the application needs (the full application runs as it in a test). Every table is classified; every append-only table has its trigger; `db-roles` is idempotent, rotates the password, and refuses short passwords, bad names, the owner, and unclassified tables | **Verified** | `backend/tests/test_db_roles.py` |
| Shared sign-in limits: sliding window; keys hashed; per-email and per-IP limits (a forged `X-Forwarded-For` cannot evade the IP limit); failures kept; success clears only that email; **separate processes share one count and a freshly started process is still blocked**; 10 simultaneous attempts record exactly 5; cleanup never lifts an active lockout, including with a longer configured window | **Verified** | `backend/tests/test_rate_limit.py` |
| Public-demo write limits: off by default; per IP across accounts; anonymous and rejected requests not counted; incidents, comments and triage requests | **Verified** | `test_rate_limit.py` |
| BUG-2 and related inputs: NUL, control characters and lone surrogates (sent as real clients send them) in every accepted string field and query parameter never cause a server error; incident text and emails return 422 | **Verified** | `backend/tests/test_input_validation.py` |
| Comment redaction: admins only (403 for others), CSRF, 404 for missing or mismatched comments, one per comment (409), reason validated and trimmed; the canary text is absent from every reader endpoint for all three roles (comments, incident, history, SLA, lists, export, analytics, triage); reason staff-only; logs identifiers only; append-only in the database; **two simultaneous redactions of one comment: exactly one 201, one 409, one record** | **Verified** | `backend/tests/test_redaction.py`; `frontend/src/components/IncidentDiscussion.test.tsx`; `e2e/tests/comments.spec.ts` |
| Session cleanup: expired and idle sessions removed, active kept; the worker loop runs it hourly | **Verified** | `test_rate_limit.py` |
| Fresh installation with the production compose file | **Verified** | see below |

**Results (local, 2026-11-16; confirmed by CI on `8ea9c72`):** backend **616 passed** (530 before; 86 new) in the CI-equivalent sequence, migration round trip clean; frontend **174 passed** (168 before; 6 new), build clean; end to end **70/70** on the development images and **70/70** against the production images with the API, worker, admin bootstrap and SLA seeding running as `imp_app` (scratch copy). Docker volumes and the development database unchanged.

**Fresh installation** (empty database volume, `deploy/compose.prod.yaml`, project `imp-freshcheck`, removed afterwards): `migrate` applied `0001`→`0010` and provisioned `imp_app`; the only role connected to the database was `imp_app`; `/api/health/ready` 200 (including the production role check). Through Caddy over HTTPS: admin bootstrap (as `imp_app`), sign-in, incident creation, status change, internal note, redaction, fake triage, history, SLA, lists, analytics, export, users, SLA policy, sign-out all succeeded, and the redacted text appeared in no response. The worker ran its SLA and hourly cleanup tasks. Connected directly as `imp_app`, PostgreSQL refused UPDATE, DELETE and TRUNCATE on audit tables, disabling or dropping triggers, `session_replication_role`, deleting users and creating tables; the redacted comment's original text remained stored.

**Migrations with data:** existing incidents, comments and users were identical (fingerprint) through `0008`→`0010`, downgrade to `0008` (both new tables and the trigger function removed) and re-upgrade; the redaction table refused UPDATE.

**Deliberate-break checks** (scratch copies; each disabled one protection): backend 19 — incident text unchecked, email control characters allowed, sign-in email unchecked, sign-in failure not committed, sign-in lock removed, limiter ignoring its window, success not clearing failures, write limits ignored, redacted body returned, reason shown to reporters, responders allowed to redact, redaction across incidents, explicit duplicate-redaction check removed, audit tables granted UPDATE, runtime role made superuser, unclassified tables allowed, readiness skipping the role check, cleanup keeping idle sessions, worker never cleaning up: **18 caught**. The worker-cleanup variant first survived; a worker-loop test was added. Frontend 3 — removed comment showing its text, removal offered to all staff, empty reason sent: **3 caught**. A further check removed the conflict handling: the concurrent-redaction test failed, as it should.

**The one survivor is equivalent by design.** Removing the explicit "already redacted" check in `redact_comment` changes nothing observable: the unique constraint `uq_comment_redactions_comment_id` still refuses a second row, and the same handler turns that `IntegrityError` into the same 409 with the same message. The explicit check only avoids a failed insert in the common, non-concurrent case. The constraint path is tested directly: two simultaneous requests, both past the explicit check, produce exactly one 201 and one 409.

### Milestone 6 Phase A — Production images, reverse proxy and readiness

Released on 2026-11-16 as `5e4548ed0cbfb1f8478efe2f927a7ed4ce16d8f3` (`feat: add production images, reverse proxy and readiness checks`, 18 files). Nothing is deployed.

**CI record:** run [43890599391](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43890599391) on `5e4548e`: **success** on the first attempt, all five jobs: Backend tests (75 s), Migration round trip (35 s), Frontend tests and build (46 s), Docker images (55 s, including the new production-image checks: non-root users, no test tools, valid compose and Caddy configuration), End-to-end tests (230 s; **69 passed** in 1.4 min of test time, from the job's annotation). The backend (530) and frontend (168) totals are from local runs of the identical tree, because job logs cannot be downloaded without repository admin rights. The production-stack checks and the end-to-end run against the production images are local.

| Functionality | Status | Evidence |
|---|---|---|
| Readiness: 200 at the migration head; 503 `migrations_pending` for an older or missing schema, `unknown_database_revision` for a newer one, `database_unavailable` when the database is down; no revisions in responses; `no-store` caching | **Verified** | `backend/tests/test_health.py`; local production stack (below) |
| Worker skips runs while the database is not ready | **Verified** | `test_health.py`; local production stack log |
| Production settings refused at start-up (insecure cookies, missing, development or short database password, paid AI provider or key, untrusted proxy setting) without revealing secrets; API docs hidden in production | **Verified** | `test_health.py` (start-up tested in a separate process) |
| Production images: non-root users, no test tools, hash-pinned dependencies, valid compose and Caddy configuration | **Verified** | CI Docker images job (new steps); local checks |
| HTTPS, redirect, security headers, caching, API routing, 1 MB body limit, no API docs, forwarded-address handling, read-only containers, no published database or backend ports | **Verified** | local production stack (below) |
| The application works unchanged behind Caddy with the production CSP and backend image | **Verified** | full end-to-end suite against the production images (below) |

**Results (local, 2026-11-16; confirmed by CI on `5e4548e`):** backend **530 passed** (510 before; 20 new) in the CI-equivalent sequence, migration round trip clean; frontend **168 passed**, build clean; end to end **69/69** on the development images and **69/69** against the production images (Caddy with the real CSP, production backend image, `APP_ENV=production`; scratch copy, with no browser console errors) with `sla-verify` consistent; `pip-audit` no known vulnerabilities (runtime lock and development requirements); actionlint clean. Docker volumes and the development database unchanged.

**Local production stack** (project `imp-prodcheck`, `SITE_ADDRESS=localhost`, removed afterwards): start-up order held (database healthy, `migrate` exit 0, backend ready); HTTP 308 to HTTPS; every header above present; `/docs`, `/redoc`, `/openapi.json` 404; a 2 MB request 413. After `alembic downgrade -1`, `/api/health/ready` returned **503 `migrations_pending`** while `/api/health` stayed 200, the backend container became unhealthy and the worker logged "skipped: database not ready"; after upgrading, 200 again. A spoofed `X-Forwarded-For` through Caddy was replaced, and one sent directly to the backend from inside the network was ignored. Containers ran as users 10001 and 10002 with read-only file systems.

**Remaining blockers before any public deployment:** least-privilege database role, shared rate limiting, input validation, comment redaction, write limits and session cleanup — Phase B (complete, `8ea9c72`); demo accounts with protection from per-email lockout, synthetic data and the daily reset — Phase C (implemented, verified locally); backups and monitoring — Phase D; scanning, image digests and the deploy workflow — Phase E; confirming real client addresses on the server — Phase F.

### Milestone 5D — Dashboard UI/UX redesign

Released on 2026-11-16 as `e3bc126e5f80609955f0e2eeebe73d92080efe32` (`feat: redesign the dashboard and add a light/dark theme toggle`, 18 files).

**CI record:** run [43871416066](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43871416066) on `e3bc126`: **success** on the first attempt, all five jobs: Backend tests (101 s), Migration round trip (37 s), Frontend tests and build (36 s), Docker images (24 s), End-to-end tests (228 s; **69 passed** in 1.3 min of test time, from the job's annotation). Each job runs its complete suite; the backend (509) and frontend (168) totals are from local runs of the identical tree, because job logs cannot be downloaded without repository admin rights. Screenshot review, colour checks and bundle sizes are local observations.

Design: *Dashboard and theme design*.

| Functionality | Status | Evidence |
|---|---|---|
| Five headline figures from the analytics response, each labelled "Selected dates" or "Right now", with fixed accents and decorative icons | **Verified** | `frontend/src/pages/DashboardPage.test.tsx`; `e2e/tests/dashboard.spec.ts` (every figure compared with the response the page received) |
| Status donut: legend with exact counts and shares, empty statuses kept in the legend and left out of the ring, keyboard reading with wrap-around, summary label | **Verified** | `DashboardPage.test.tsx`; `dashboard.spec.ts` |
| Severity bars coloured by severity with named rows; SLA panel with on-time rates, overdue counts and staff-only escalations | **Verified** | `DashboardPage.test.tsx`, `IncidentListSla.test.tsx`; `dashboard.spec.ts` |
| Role-specific figures: a reporter sees only their own incidents and never escalations | **Verified** | `DashboardPage.test.tsx`; `dashboard.spec.ts` |
| Presets, custom range validation, URL persistence, trend keyboard readout and table view unchanged | **Verified** | existing unit and end-to-end tests, unchanged and passing |
| Theme: follows the operating system until chosen; keyboard-operable toggle with state; choice stored per browser, restored before the first render, overriding the operating system both ways; unknown stored values ignored; works without storage or `matchMedia` | **Verified** | `frontend/src/theme.test.tsx` (5 tests); `e2e/tests/theme.spec.ts` (2 tests, computed page colours) |
| Accessibility: axe WCAG 2.1 A/AA scans of every page in both operating-system themes, plus the dashboard with the opposite theme chosen in the header and the donut focused; zero serious or critical violations | **Verified** | `e2e/tests/accessibility.spec.ts` |
| Responsive: no horizontal scrolling on the dashboard and main pages at 390, 768, 1,024 and 1,280 px; every headline card inside the viewport | **Verified** | `accessibility.spec.ts` (3 new width tests; the 390 px test already covered the dashboard) |
| Colour checks: chart palettes validated with the dataviz validator in both themes; every new graphic pairing at least 3:1 and text at least 4.5:1 (computed) | **Verified** | see *Colour checks* below |

**Results (local, 2026-11-16; confirmed by CI on `e3bc126`):** frontend **168 passed** (158 before; 10 new), type check and production build clean; backend **509 passed** (unchanged; no backend changes); end to end **69 tests** (61 before; 8 new): **three consecutive runs, 69/69 each**, on freshly built disposable stacks, with `sla-verify` consistent before and after. The Docker volume list matched the baseline before and after (an interrupted earlier run had left `imp-e2e_postgres_data`, which the runner removed after confirming it belonged to the end-to-end project).

**Colour checks:** the status badge tones failed as a chart palette (blue and purple ΔE 2.0 for deuteranopia; grey below the chroma floor), so the donut uses reference-palette slots. Light-mode aqua and yellow were 2.82:1 and 2.17:1 on white; the palette's documented deeper steps (`#199e70`, `#c98500`) pass every check in both themes (worst neighbouring ΔE 8.4, contrast at least 3:1). Accents, icons on their tints, severity bars on cards and tracks: at least 4.57:1 in dark and at least 3:1 in light.

**Screenshot review** (both themes at 390, 768, 1,024 and 1,280 px, the reporter view, the focused donut): two layout defects were found and fixed before the final runs. At 1,280 px the header wrapped onto two rows once the theme toggle was added (container widened to 1,240 px), and the SLA panel's fourth tile wrapped, leaving the severity card mostly empty (severity and backlog now stack beside the SLA panel; SLA tiles fit four to a row). At 390 and 768 px the fifth card now spans the row instead of standing alone. Unchanged and noted: the trend chart's y-axis rounds up to the next "nice" maximum (40 for a peak of 12), as before.

**Performance:** no new requests (one analytics request per range, as before) and no new dependencies. Production bundle: JavaScript 326.8 kB (95.8 → 98.3 kB gzipped, +2.5 kB), CSS 27.5 kB (5.4 → 6.2 kB gzipped). The donut is static SVG; the only motion is a 0.12 s segment emphasis, disabled under reduced motion.

**Deliberate-break checks** (scratch copies; each disabled one protection): frontend 11 variants — wrong field behind a headline figure, scope label missing, donut legend without counts, donut not keyboard-operable, donut without a text alternative, escalations shown to reporters, severity bars uncoloured, theme not persisted, operating-system theme ignored, stored theme not restored, trend table removed: **11 caught**. End to end 2 variants — wrong headline field, theme not persisted: **2 caught**. The wrong-field variant first survived end to end, because every end-to-end incident is created inside the default range; the reporter test now also opens a range before any incident (backlog still 1, range figures 0).

**Deviations from the approved plan, recorded:**
- **Theme toggle added** (decision 2, not in the original plan): `src/theme.ts`, a header button and `light-dark()` tokens replacing the media-query block.
- **Donut colours** are chart-palette slots, not the badge tones (failed the colour-vision check).
- **Layout:** the page container is 1,240 px instead of 1,120 px on every page; current backlog moved beside the SLA panel.
- **Documentation fix:** the roadmap table still listed 5C as Planned after its release; corrected here.

### Milestone 5C — AI-assisted incident triage

Released on 2026-11-15 as `4a216d716b98002ca4947c62495e476807ae0d06` (`feat: add AI-assisted incident triage with human review`, 37 files). The real provider stays off by default; no paid API call has been made: every automated check uses the deterministic fake provider or a stand-in SDK client.

**CI record:** run [43784327150](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43784327150) on `4a216d7`: **success** on the first attempt, all five jobs: Backend tests (87 s), Migration round trip (32 s, including upgrade to `0008` and downgrade to base), Frontend tests and build (35 s), Docker images (29 s), End-to-end tests (214 s; **61 passed** in 1.1 min of test time, from the job's annotation). Each job runs its complete suite; the backend (509) and frontend (158) totals are from local runs of the identical tree, because job logs cannot be downloaded without repository admin rights. The benchmark figures below are local measurements; CI does not run the benchmark.

Design: *AI triage design*; data model: `triage_requests`, `triage_suggestions`, `triage_decisions`, `incidents.search_vector`.

| Functionality | Status | Evidence |
|---|---|---|
| Off by default; unknown provider names are off; the real provider needs a key and known prices (fails closed for other models); constructed with timeout 20 s and one retry; never called in tests | **Verified** | `backend/tests/test_triage.py` |
| Requests are minimal and strict: no tools or sampling parameters, strict JSON schema, effort low, thinking off; refusals, truncation, non-JSON, missing usage, timeouts, connection errors, 429 and 5xx are mapped; one call on the configured model, no fallback | **Verified** | `test_triage.py` (stand-in SDK client, no network) |
| Strict output validation: 29 malformed and adversarial shapes rejected (keys, severity, lengths, control characters, URLs, web and email addresses, duplicate ids not sent or not integers); unusable results recorded with a category and a plain message; no raw output stored or logged | **Verified** | `test_triage.py` |
| Prompt-injection defences: incident text sent as one JSON value marked untrusted, separate from the instructions; canaries never reach logs or responses | **Verified** | `test_triage.py`, `test_triage_eval.py` |
| Data minimisation: four incident fields and up to five visible candidate titles; never comments, internal notes, people or SLA data (canary test); candidates restricted to the requester's visibility | **Verified** | `test_triage.py` |
| Human review: requesting never changes the incident; accept, accept with edits, internal note (comment rules apply, invalid note changes nothing), reject with reason, accepting the current severity records no change; one decision per suggestion; stale suggestions refused (409) | **Verified** | `test_triage.py`, `e2e/tests/triage.spec.ts` |
| Audit and SLA: the accepted change is an ordinary audit event with the deciding person as actor (not the requester); SLA targets follow through the shared update path and the stored read model stays consistent; the history marker is staff-only | **Verified** | `test_triage.py`, `triage.spec.ts` |
| Access: reporters get 403 on every triage endpoint and see no panel, marker or note; CSRF on both state-changing routes (route inventory); 404 for missing incidents and suggestions | **Verified** | `test_triage.py`, `test_auth.py`, `triage.spec.ts` |
| Cost controls: costs in micro-dollars rounded up; worst-case reservation is an upper bound; actual cost recorded; unknown billing charged in full; every limit (cooldown, hourly, per incident, per user per day, global daily count and spend, monthly spend) stops requests before the provider is called, with `Retry-After` | **Verified** | `test_triage.py` |
| Concurrency and crashes: no transaction, row lock or budget lock is held while the provider runs (checked from another connection); reservations are serialised by the advisory lock; simultaneous requests cannot overspend; an in-flight reservation counts at full cost; crashed reservations are closed as abandoned at full cost after 600 s and a late result keeps that record | **Verified** | `test_triage.py` (real transactions) |
| Append-only triage tables (UPDATE, DELETE, TRUNCATE refused) and consistency checks on suggestions and decisions | **Verified** | `test_triage.py`; migration check below |
| Triage panel: source labelled in text, the fake never presented as AI, "What is sent" disclosure, allowance, plain-text rendering of model output, editable severity and note with counter and validation, reject with reason, stale and rate-limit messages, focus to the result and to the decision, earlier suggestions collapsed | **Verified** | `frontend/src/components/TriagePanel.test.tsx` (17 tests) |
| End to end with the fake provider: review, edit and accept with audit, note and reporter view; reject; stale; cooldown message; CSRF | **Verified** | `e2e/tests/triage.spec.ts` (5 tests) |
| Accessibility: axe scans of the review form, decision and rate-limit message in both themes; 390 px layout with the review form open | **Verified** | `e2e/tests/accessibility.spec.ts` (2 new tests, phone test extended) |
| Evaluation harness and synthetic dataset; paid runs refused without `--confirm-spend` and a sufficient `--max-usd` | **Verified** | `backend/tests/test_triage_eval.py`, `backend/eval/triage_cases.jsonl` |
| Migration `0008` upgrade, downgrade, re-upgrade with data; `alembic check` | **Verified** | see *Migration* below |
| Stored search vector: generated, GIN-indexed, follows title and description changes on create and update, matches description words, never in API responses or exports | **Verified** | `test_triage.py`; migration check below |
| Duplicate-candidate search under 100 ms on the benchmark dataset (after the approved stored-vector change) | **Verified** | see *Benchmark* below |
| The API key never appears in triage responses or logs (canary key), nor in built frontend assets (built with a canary key in the environment); the frontend reads no environment variables | **Verified** | `test_triage.py`; asset scan |

**Results (local, 2026-11-15; confirmed by CI on `4a216d7`):** backend **509 passed** (392 before; 117 new) in the CI-equivalent sequence; frontend **158 passed** (141 before; 17 new), type check and production build clean; end to end **61 tests** (54 before; 7 new): **three consecutive runs, 61/61 each**, on freshly built disposable stacks (2.2–2.3 min of test time), repeated after the stored-vector change, with `sla-verify` consistent before (6 cycles) and after (73 cycles) each run. The Docker volume list was identical before and after. An earlier full run failed on two new accessibility checks, both fixed before the recorded runs: a test locator that matched two labels, and an existing contrast defect found by the new scan (below).

**Evaluation (fake provider, deterministic; checks the pipeline, not quality):** 45 cases; schema-valid 100%; exact severity 71.1%; within one level 88.9%; injection-resistant 91.7% (11 of 12: the Spanish-language case has no English keyword, so the keyword rules say "low" for a "critical" label; no canary or injected text appeared in any output); duplicate precision 88.9%, recall 100%. Against the thresholds for a real model (98 / 70 / 95 / 100 / 80%), the fake fails within-one and injection resistance, as expected of keyword rules. **The real model has not been evaluated**: a run needs separate approval (worst case $0.035 per run, $0.105 for three runs on Haiku 5.5).

**Benchmark (52,000 incidents, 40,000 triage requests this month; PostgreSQL 17, default settings; medians of 7 after 2 warm-ups):** varied titles from a 70-word security vocabulary, plus 2,000 identical titles as a duplicate-heavy case. The first design (an expression index, ranking recomputed per row) missed the target; Option A, a stored generated `tsvector` column, was approved on 2026-11-15 and implemented in `0008`.

| Request | First design | Stored vector (released design) | Target |
|---|---|---|---|
| Duplicate candidates, staff, 10 titles | 180–394 ms | **27–86 ms** (slowest single sample 97 ms) | < 100 ms |
| Duplicate-heavy title (2,000 identical) / largest reporter (5,529 incidents) | 188 / 28 ms | **31 / 7 ms** | < 100 ms |
| Budget checks (`check_limits`) over 40,000 requests | 26 ms | 28 ms | — |
| `POST /incidents/{id}/triage-suggestions` with the fake provider | 234 ms | **99 ms** | — |
| `GET /incidents/{id}/triage-suggestions` / `GET /triage/status` | 16 / 11 ms | 16 / 13 ms | — |

Query plans (`EXPLAIN ANALYZE` of the exact query, 12 staff and 3 reporter titles): a selective query (2 matching incidents) uses a bitmap scan of `ix_incidents_search` (3.4 ms); the broad queries in this dense dataset match 15,000–21,000 of 52,000 incidents (29–41%), where the planner reads the stored vectors sequentially (25–37 ms; forcing the index gives 14–35 ms, both well within the target); reporter queries start from `ix_incidents_reporter_id` (about 4 ms). The benchmark's medians include the application round trip.

**Migration:** upgrade `0001`→`0008`, `alembic check` clean (no exclusions), downgrade to base leaves no tables or enum types, re-upgrade and check clean. With existing data created at `0007` (an audited incident, a legacy incident, an audit event and an internal note): a fingerprint (MD5 over every incident, event and comment field) was **identical** before and after upgrading to `0008`, and both incidents received a correct search vector. After upgrading: a title update recomputed the vector, a direct write to it was refused by PostgreSQL, and the triggers refused UPDATE on triage rows. Downgrading to `0007` removed the column, the index and every triage table, enum and function with the fingerprint unchanged; re-upgrading recomputed every vector (2 of 2 correct) with the fingerprint still unchanged. **Downgrading `0008` deletes triage records.**

**Deliberate-break checks** (scratch copies; each disabled one protection): backend 21 variants — severity enum unchecked, unknown duplicates allowed, links allowed, staleness skipped, suggestion applied automatically, requester as actor, comments sent to the model, candidates without visibility, reporters allowed, history marker for reporters, cooldown skipped, unresolved reservations free, budget lock removed, transaction held during the call, unknown billing free, abandoned reservations free, fallback to a more expensive model, real provider on by default, raw output logged, plus the explicit existing-decision check removed: **20 caught**. The survivor is equivalent by design (the unique constraint still refuses a second decision, and that path is tested). The budget-lock variant first survived; a deterministic lock test was added before the final runs. Frontend 7 variants — model text as HTML, test provider labelled AI, panel for reporters, stale suggestion acceptable, accepted without review, incident not reloaded, history marker missing: **7 caught** (one needed a new explicit assertion). End to end 2 variants — reporters allowed, staleness skipped: **2 caught**. Search vector 1 variant — title only, in model and migration: **caught** by 3 tests.

**Deviations from the approved plan, recorded:**
- **SDK version:** `anthropic` 2.8 (current) instead of the 1.x assumed in the plan; it depends on `httpx2`, separate from the `httpx` used by the tests.
- **Existing tests changed:** the TRUNCATE assertions in `test_audit.py` and `test_comments.py` now also accept PostgreSQL's foreign-key refusal, which fires before the append-only trigger now that triage tables reference events and comments. The CSRF route inventory in `test_auth.py` lists the two new routes.
- **Search storage (approved 2026-11-15, Option A):** the planned expression index was replaced by a stored generated column `incidents.search_vector` with a GIN index, because the first design missed the < 100 ms target. `alembic check` needs no exclusions.
- **Shared code paths:** `incidents.apply_incident_changes` and `comments.add_comment` were extracted from the existing endpoints so accepting a suggestion uses exactly the same audited and SLA-tracked code; endpoint behaviour is unchanged (full suites pass).
- **Contrast fix outside triage:** the light-theme Medium severity badge was 3.9:1 on the page background (WCAG AA needs 4.5:1); it was never scanned before because earlier accessibility tests used High incidents. `--sev-medium` is now `#854d0e` (5.3:1).
- **Compose:** `compose.yaml` passes `TRIAGE_PROVIDER` (default `off`) and `ANTHROPIC_API_KEY` to the backend; the end-to-end override forces `fake` and an empty key. `.env.example` documents both (commented). The backend image now includes `eval/`.

**Security review of changed files:** no credentials or real data (synthetic `example.com` users, canary strings, a placeholder test key string that is never sent); no generated artifacts. New dependency `anthropic` 2.8: `pip-audit` no known vulnerabilities; `npm audit` 0 for `frontend` and `e2e` (unchanged); the workflow is unchanged; backend and frontend images build.

### Milestone 5B — SLA tracking and escalation

Released on 2026-11-15 as `d0ea3e8a87eb27baa67b2ddd661d1b6353bfadc6` (`feat: add SLA tracking and escalation`, 50 files).

**CI record:** run [43766899182](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43766899182) on `d0ea3e8`: **success** on the first attempt, all five jobs: Backend tests (71 s), Migration round trip (28 s, including upgrade to `0007` and downgrade to base), Frontend tests and build (23 s), Docker images (29 s), End-to-end tests (207 s; **54 passed** in 1.1 min of test time, from the job's annotation). Each job runs its complete suite; the backend (390) and frontend (141) totals are from local runs of the identical tree, because job logs cannot be downloaded without repository admin rights. The performance figures below are local measurements; CI does not run the benchmark.

Design: *SLA design*; data model: `sla_policy_versions`, `sla_policy_targets`, `incident_sla_cycles`, `incident_escalations`.

| Functionality | Status | Evidence |
|---|---|---|
| Exact boundaries with an injected clock: warning at 75%, due exactly now is overdue, satisfied exactly at the deadline is met, 1 µs later breached | **Verified** | `backend/tests/test_sla.py` (boundary tests) |
| Acknowledgment rules: status change from open or assignment, staff-created with assignee, comments, internal notes and reporter edits never count, never reversed | **Verified** | `test_sla.py` |
| Reopen cycles (all four resolved/closed → open/investigating moves), multiple reopens, response in cycle 0 only, earlier outcomes preserved and reconstructible | **Verified** | `test_sla.py`, `test_sla_store.py` |
| Strictest severity per target from the original start; lowering never relaxes; changes after satisfaction ignored; policy frozen at cycle start, boundary at equal timestamps and ties by id | **Verified** | `test_sla.py`, `test_sla_policy.py` |
| Stored read model equals the audit-derived reference in every field, state and filter (every scenario test, 60 random histories at 11 instants, migration backfill of 40 random histories, a reopened pre-policy incident, legacy incidents) | **Verified** | `test_sla_store.py`, teardown check in `test_sla.py` and `test_escalation.py`; `sla-verify` before and after each end-to-end run |
| History guard: no deletes or truncation, completed cycles final, start and version frozen, acknowledged response final, deadlines only stricter | **Verified** | `test_sla_store.py` |
| Transaction rollback: a failure after the SLA update rolls back the incident, its event and its cycle | **Verified** | `test_sla_store.py::test_a_failed_change_rolls_back_the_incident_its_event_and_its_sla_cycle` |
| Concurrent incident updates (12 simultaneous requests) are serialised and the read model stays consistent | **Verified** | `test_sla_store.py::test_concurrent_updates_are_serialised_and_the_read_model_stays_consistent` |
| Escalation: once per target, only when overdue, never retrospective, per cycle, untracked/closed/legacy ignored, no change to the incident or its history, logs without content | **Verified** | `backend/tests/test_escalation.py` |
| Escalation concurrency: resolution committed while the worker waits, worker first then resolution, two concurrent escalations of one target, two concurrent worker runs, policy change waits for in-flight incident writes | **Verified** | `test_escalation.py` (real transactions) |
| Admin-only policy versions with reason, validation (strict integers, bounds, ordering), CSRF, append-only history; new versions apply only to newly started cycles | **Verified** | `backend/tests/test_sla_policy.py`; `test_auth.py` CSRF inventory |
| RBAC and confidentiality: incident visibility (same 404), staff-only escalation details and filter, reporter-scoped filters, dashboard and export, no internal notes in any SLA response, list, dashboard or export | **Verified** | `test_sla.py` (canary and scope tests); `e2e/tests/sla.spec.ts` |
| SLA card (server clock, never the browser clock, 60 s refresh, states as text, reopen note, earlier periods, staff-only escalations), list column and URL filter, dashboard SLA section, admin policy page with review step | **Verified** | `frontend/src/components/IncidentSla.test.tsx`, `pages/IncidentListSla.test.tsx`, `pages/SlaPolicyPage.test.tsx`, `validation.test.ts` (28 new tests) |
| End to end: seeded scenarios, real escalation command run twice (1 then 0 recorded), staff filters and escalation, reporter view without escalations, outcomes of met/breached/reopened incidents, cross-reporter 404, admin policy change and refusals | **Verified** | `e2e/tests/sla.spec.ts` (6 tests), `e2e/seed_sla.py`, `e2e/run.sh` |
| Accessibility: axe scans of the SLA card with an escalation, the SLA-filtered list and the policy page (error and review states) in both themes; 390 px layouts | **Verified** | `e2e/tests/accessibility.spec.ts` (2 new tests, phone tests extended) |
| Migration `0007` upgrade, downgrade, re-upgrade with data and backfill; `alembic check` | **Verified** | see *Migration* below |

**Results (local, 2026-11-15):** backend **390 passed** (269 before; 121 new) in the CI-equivalent sequence; frontend **141 passed** (113 before; 28 new), type check and production build clean; end to end **54 tests** (45 before; 9 new): **three consecutive runs, 54/54 each**, on freshly built disposable stacks (about 2 minutes of test time; 227–237 s per run including build and teardown). In every run the real escalation command recorded 1 escalation and then 0, and `sla-verify` reported the stored cycles consistent with the audit trail before the suite (6 cycles) and after it (65 cycles). The Docker volume list was identical before and after. An earlier full run failed on three items, all fixed before the recorded runs: the escalated-filter defect, the export column list (new SLA columns) and the dashboard tile wording (see below).

**Benchmark (50,000-incident dataset).** The Milestone 3 seed with its shape unchanged (50,000 incidents, 225,547 events; 20,144 open or investigating, 29,856 resolved or closed, 5,000 legacy, 9,712 reopened; largest reporter 263 incidents), with two additions so SLA can be measured: `created` events record the initial severity, as the API always does, and a policy version three years old. Backfill of the stored cycles: 47,591 cycles (2,591 reopened) in 1.9 s; consistency check of every cycle in 2.7 s: **0 mismatches**. Medians of 7 requests after 2 warm-ups, through the real endpoints (PostgreSQL 17.12, default settings):

| Request | Audit-derived (first design) | Stored read model | Target |
|---|---|---|---|
| Staff `?sla=overdue` / `approaching` / `breached` / `escalated` | 243 / 236 / 590 / 229 ms | **51 / 45 / 64 / 13 ms** (59 ms escalated after 29,544 escalations) | < 300 ms |
| Staff dashboard 30 days / 366 days | 352 / 748 ms | **110 / 222 ms** | < 300 ms |
| Reporter filters and 366-day dashboard | 62–89 ms | **45–71 ms** | < 300 ms |
| Worker: one bounded run (500 targets, 5 batches) | 7.8 s | **148 ms** (batches 19–27 ms) | < 1 s |
| Worker: clearing a 29,524-target backlog | ~8 min (estimated from 15.7 ms per target) | **7.9 s** in 61 runs (median run 129 ms, max 218 ms; batch median 16.7 ms, max 113 ms) | < 1 s per run |
| Worker: steady state / 20 newly overdue targets | 385 / 612 ms | **27 / 40 ms** | < 1 s |

Query plans (stored model): the approaching and escalated filters use the partial indexes (`ix_incident_sla_cycles_open_response`, `_open_resolution`) and run in under 1 ms; overdue, breached, the dashboard backlog and 366-day figures scan the 47,591-row cycle table (16–32 ms) because they match most open incidents in this dataset; the worker's due-target query takes 26 ms. Earlier findings on the way: the first derived formulation took 54 s for the overdue filter (a policy subquery repeated inside every state expression, which also triggered 4.4 s of JIT compilation); it was rewritten before the stored model was approved.

**Migration:** upgrade `0001`→`0007`, `alembic check` clean, downgrade to base leaves no tables or enum types, re-upgrade and check clean. With data present (an incident created through the write path, a policy and a cycle): the append-only policy table refused an UPDATE; downgrading to `0006` removed every SLA table, enum and function while incidents, events and comments remained; re-upgrading backfilled the incident's cycle (untracked, because it started before the new policy) and `sla-verify` reported it consistent.

**Deliberate-break checks** (scratch copies; each disabled one protection): backend 19 variants — SLA detail without incident visibility, escalations shown to reporters, escalated filter or flag for reporters, policy change without the admin check, write path without the policy lock, worker ignoring target state, worker without the row lock, reopen not starting a cycle, severity relaxing a target, the satisfying event's severity counted, policy not frozen, comments counted as acknowledgment, strict deadline boundary, strict overdue filter boundary, escalated filter uncorrelated, breached filter ignoring the response, dashboard counting every cycle: **18 caught**. The survivor removes `ON CONFLICT DO NOTHING` from the worker insert; it is equivalent in behaviour, because the recheck runs under the incident row lock and sees any committed escalation, and the unique constraint is tested directly. Two of the caught variants (dashboard, filter boundary) needed new tests, added before the final runs. Frontend 6 variants — browser clock used, escalated filter offered to reporters, state shown by colour only, policy saved without the review step, ordering rule removed, no periodic refresh: **6 caught**. End to end 3 variants — escalated filter uncorrelated, escalations shown to reporters, policy change without the admin check: **3 caught**.

**Defects found and fixed during implementation (new code, before any commit):**
- **Performance:** see the benchmark; the stored read model and batched worker were approved and implemented.
- **Dashboard cross join:** the first-cycle figures filtered on the unaliased cycle table, multiplying counts (reporter test caught it).
- **Escalated filter correlation:** the current-cycle subquery took the highest cycle of all incidents, so the staff "Escalated" filter showed nothing (end-to-end test caught it; regression tests added).
- **Reference scope:** the audit-derived reference emitted untracked cycles for legacy incidents that had later status changes, while the store (correctly) has none: 4,088 differences in the 50,000-incident backfill check. The reference now excludes legacy incidents; 0 differences.
- **Planner statistics during bulk escalation:** two runs took about 1 s while clearing the 29,524-target backlog because `incident_escalations` statistics lagged its growth; the worker now analyses it after a full run (max run 218 ms).
- **Wording:** the dashboard's SLA tiles said "still open", which an existing end-to-end locator also matched; they now say "outstanding".

**Security review of changed files:** no credentials or real data (synthetic `example.com` users, random per-run passwords for the end-to-end SLA reporter, canary strings); no generated artifacts in the change set. No new dependencies; `pip-audit` no known vulnerabilities; `npm audit` 0 for `frontend` and `e2e`; actionlint no findings; backend and frontend images build.

### Milestone 5A — Incident comments and internal notes

Released on 2026-11-15 as `ddc571edd51ea58a7d1862ca9ab592105020f615` (`feat: add incident comments and internal notes`).

**CI record:** run [43749980036](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43749980036) on `ddc571e`: **success** on the first attempt, all five jobs: Backend tests (49 s), Migration round trip (38 s, including upgrade to `0006` and downgrade to base), Frontend tests and build (27 s), Docker images (25 s), End-to-end tests (171 s; **45 passed** in 48.2 s of test time, from the job's annotation). Each job runs its complete suite; the backend (269) and frontend (113) totals are from local runs of the identical tree, because job logs could not be downloaded without repository admin rights.

Design: *Comments and internal notes design*; data model: `incident_comments`.

| Functionality | Status | Evidence |
|---|---|---|
| Reporters read and post public comments on their own incidents only; responders and admins read and post both kinds on every incident, including legacy ones | **Verified** | `backend/tests/test_comments.py` (role tests); `e2e/tests/comments.spec.ts` (conversation test) |
| Internal notes and their count never reach a reporter: comment list and every page, incident detail and list, history, analytics, CSV export, `/auth/me` and error responses | **Verified** | canary and paging tests in `test_comments.py`; end-to-end API, UI, dashboard and CSV checks |
| Identical 404 for another reporter's, a legacy and a missing incident on GET and POST (including a request for an internal note); reporter internal posting refused with 403 | **Verified** | `test_comments.py`; `comments.spec.ts` (UI and API) |
| Author from the session only (`author_id`, `created_at`, `id`, `incident_id` refused); `visibility` required; CSRF required on POST (route inventory updated) | **Verified** | `test_comments.py`; `test_auth.py` CSRF inventory and per-route test |
| Append-only: no edit or delete routes; trigger blocks UPDATE, DELETE, TRUNCATE; references RESTRICT; no default visibility | **Verified** | `test_comments.py` |
| Adding comments leaves the incident row (including `updated_at`) and `incident_events` untouched; analytics and export output unchanged | **Verified** | `test_comments.py` |
| Body rules identical in the API and PostgreSQL, including Unicode, NUL, control characters, lone surrogates and length in code points | **Verified** | `test_comments.py` (agreement tests: 40 direct inserts and about 50,000 generated bodies) |
| Oldest-first ordering with ID tie-breaker, stable paging, parameter validation | **Verified** | `test_comments.py` |
| Fail-closed response check | **Verified** | `test_comments.py::test_a_regressed_query_fails_closed_instead_of_leaking` |
| Discussion UI: plain-text bodies, internal-note marking in text, staff default Internal note, named submit button, public-reply warning, reporter has no visibility choice, validation and counter matching the server, announcements, focus, loading, empty, error and Retry, "Show more comments" without duplicates | **Verified** | `frontend/src/components/IncidentDiscussion.test.tsx` (17 tests); `frontend/src/validation.test.ts` (5 comment tests) |
| Discussion end to end: conversation between roles, keyboard visibility choice, HTML as text, API refusals | **Verified** | `e2e/tests/comments.spec.ts` (5 tests) |
| Accessibility: axe WCAG 2.1 A/AA scan of the discussion (staff view with an internal note, public-reply warning and a field error; reporter view) in light and dark themes; long unbroken comment text fits at 390 px | **Verified** | `e2e/tests/accessibility.spec.ts` (2 new tests, phone-width test extended) |
| Migration `0006` upgrade, downgrade and re-upgrade; `alembic check` | **Verified** | see *Migration* below |

**Results (local, 2026-11-15):** backend **269 passed** (165 before; 104 new) on a disposable PostgreSQL 17 in the CI-equivalent sequence; frontend **113 passed** (91 before; 22 new), type check and production build clean; end-to-end **45 tests** (38 before; 7 new): **three consecutive runs, 45/45 each**, on freshly built disposable stacks (about 1.6 minutes of test time; 194–199 s per run including build and teardown). The Docker volume list was identical before and after the runs. Two earlier full runs during development failed on test mistakes (a substring label match, an AxeBuilder page created with `browser.newPage()`, a 405 probe not allowed by the console guard) and on the contrast defect below; all were corrected before the three recorded runs.

**Migration:** on a disposable database: upgrade `0001`→`0006`; `alembic check` reported no differences; downgrade to base left no application tables or enum types; re-upgrade and `alembic check` clean again. Separately, with a comment row present, downgrading to `0005` removed the table, the `comment_visibility` type and the trigger function while incidents and the `0005` export triggers remained; upgrading again recreated `0006` and `alembic check` was clean.

**Deliberate-break checks** (scratch copies only; each removed exactly one protection):

| Protection removed | Caught by |
|---|---|
| Reporter visibility filter (rows and total) | 2 backend tests (fail-closed check turned the leak into a 500); end to end: 2 tests (with the fail-closed check also removed) |
| Reporter filter and fail-closed check together | 3 backend tests |
| Filter on the reporter's `total` only (internal-note count) | 1 backend test |
| 403 for reporter internal notes (unauthorized posting) | 2 backend tests |
| Incident authorization (`_get_visible` replaced by a plain lookup) | 1 backend test; end to end (visibility rule removed): 2 tests |
| Visibility checked after the internal-note 403 (existence leak) | 1 backend test |
| Unknown request fields ignored instead of refused (author spoofing) | 1 backend test |
| API blank set missing U+3000 (API and database disagree) | 4 backend tests |
| NUL not rejected by the API | 5 backend tests |
| Incident `updated_at` set when commenting; incident row rewritten without value changes | 1 backend test each (the first version of this test missed both because tests share one transaction; it now backdates the row and compares its `ctid`) |
| Comment body rendered as HTML | 1 frontend test; 1 end-to-end test |
| Staff default visibility public; reporter offered the visibility choice; "Not visible to the reporter" removed; public-reply warning removed; client validation removed | 5, 1, 2, 1 and 1 frontend tests |

All 18 variants were caught.

**Defects found and fixed during implementation (new code, before any commit):**
- **Focus:** after posting, focus was meant to return to the textarea but stayed on the button, because the textarea was still disabled when focus was requested. Focus now moves after the re-enabled textarea renders (frontend test).
- **Contrast (WCAG 1.4.3):** axe found the light-theme "Internal note" badge at 3.9:1 (amber `#a16207` on its tint over the note's own tinted background). A dedicated `--internal` tone (`#92400e` light, 5.4:1; `#fcd34d` dark, 7.9:1) replaced it.

**Existing defect confirmed (not fixed; reported separately as BUG-2):** a NUL character in an incident title or description returns HTTP 500 (see *Known issues*).

**Security review of changed files:** no credentials, tokens or real personal data (synthetic `example.com` users and canary strings only); no generated artifacts (test results, reports, builds, `node_modules`) in the change set; no changes outside the milestone apart from the CSRF route inventory and test helpers. No new dependencies; `pip-audit` (backend) found no known vulnerabilities; `npm audit` found 0 for `frontend` and `e2e`. Backend and frontend Docker images build.

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
| CI | Backend tests on a disposable PostgreSQL; migration round trip including the admin bootstrap command; frontend tests, type check and build; Docker image builds | **Verified** | Latest: GitHub Actions run [43766899182](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43766899182) on `d0ea3e8` (Milestone 5B): all five jobs **success** (End-to-end tests: 54 passed). Milestone 5A: run [43749980036](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43749980036) on `ddc571e` (Milestone 5A): all five jobs **success** (End-to-end tests: 45 passed). Milestone 5: run [43726750357](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43726750357) on `dea4ba7` (Milestone 5): all **five** jobs **success**, including End-to-end tests (38 passed). Milestone 4: run [43717432124](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43717432124) on `02eba64`: all four jobs **success**. Milestone 3: run [43714418907](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43714418907) on `7240e3a`: all four jobs **success**. Milestone 2: run [43672524737](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43672524737) on `ec3ed2e`: all four jobs **success**. Milestone 1: run [43668840665](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43668840665) on `1ae693b`: Backend tests, Migration round trip (upgrade, downgrade, schema removed, re-upgrade, `alembic check`, bootstrap admin), Frontend tests and build, and Docker images all **success**. Earlier: run [43665186312](https://github.com/BonjourCoder-JD/incident-management-platform/actions/runs/43665186312) on `65d2185`. |
| Supply chain | Lockfile with `npm ci`; read-only CI token; no repository secrets | **Implemented** | `frontend/package-lock.json`, `ci.yml` |

**Test counts:** 390 backend (pytest), 141 frontend (Vitest + React Testing Library), 54 end-to-end (Playwright), including Milestone 5B (CI run 43766899182: all five jobs passed; 54 end-to-end tests passed in CI). After Milestone 5A (CI run 43749980036, all five jobs passed): 269, 113 and 45. After Milestone 5: 165, 91 and 38. After Milestone 3: 122 and 78. After Milestone 2: 98 and 68. After Milestone 1: 72 and 60. Before Milestone 1: 6 and 35.

The manual browser checks were one-off Playwright scripts, not part of the repository or CI. Milestone 5 makes them permanent.

---

## Known issues

| ID | Issue | Impact | Resolution |
|---|---|---|---|
| ~~BUG-1~~ | ~~`assignee_id` above 2147483647 returned HTTP 500 instead of 422.~~ | — | **Fixed in Milestone 1** (`le=2147483647` on create and update schemas; regression tests). |
| ~~BUG-2~~ | ~~A NUL character in an incident `title` or `description` returned HTTP 500.~~ | — | **Fixed in Milestone 6 Phase B**: 422 for control characters in incident text and emails; a sweep of every accepted string input. |
| ~~ISSUE-2~~ | ~~No authentication or authorization.~~ | — | **Resolved in Milestone 1.** |
| ISSUE-3 | `StarletteDeprecationWarning`: using `httpx` with `starlette.testclient` is deprecated in favour of `httpx2`. | Test warning only. | Milestone 6 dependency review. |
| ~~ISSUE-4~~ | ~~`/health/db` creates a new engine per request and duplicates a fallback database URL.~~ | — | **Fixed in Milestone 6 Phase A** (shared engine, no fallback URL). |
| ISSUE-5 | The development stack runs the Vite dev server as root and does not apply migrations automatically. | Development only. | **Production stack fixed in Milestone 6 Phase A** (static build behind Caddy, non-root images, `migrate` service and readiness gate); the development stack is unchanged by design. |
| ISSUE-6 | When the backend is down, the Vite proxy returns 500, so the UI says "server error" rather than "unreachable". | Minor wording. | Milestone 6 (production proxy). |
| ~~ISSUE-7~~ | ~~The login rate limiter was in memory, per process, and saw only the proxy's address.~~ | — | **Fixed in Milestone 6 Phases A and B**: PostgreSQL-backed, shared and restart-safe; client address trusted only from the proxy. Real client addresses on the server are confirmed in Phase F. |
| ISSUE-8 | No self-service password reset, and no forced password change after an admin sets a temporary password. | Admins must share temporary passwords out of band. | Future milestone (needs email delivery); forced first-login change could come in Milestone 6. |
| ~~ISSUE-9~~ | ~~Expired session rows were deleted only when presented or at the user's next sign-in.~~ | — | **Fixed in Milestone 6 Phase B**: hourly cleanup by the worker. |
| ISSUE-10 | Demoting a responder or admin to reporter does not unassign incidents already assigned to them. | Stale assignment visible to staff. | Decided in Milestone 2: deferred to Milestone 6 (automatic unassignment on demotion or deactivation, recorded as `assignment_changed` events with the admin as actor). |
| ISSUE-12 | In production the app runs as the least-privilege `imp_app` role, which cannot modify, remove or unprotect audit records (**Milestone 6 Phase B**). The schema owner is still the PostgreSQL image's superuser, and the development stack still connects as the owner. | A compromised owner credential could still alter the triggers. | Optional later: a non-superuser schema owner; ship events to external append-only storage. |
| ISSUE-11 | `Secure` cookies work over plain HTTP only on `localhost` (a browser exception). | Any other host must use HTTPS. `SESSION_COOKIE_SECURE=false` exists for local testing only. | Milestone 6 deployment uses HTTPS. |
| ISSUE-13 | The analytics "reopened" set is built from every audited reopening on each request (about 19 ms at 225k events), and PostgreSQL underestimates row counts for the JSONB status predicates, so the staff 366-day cohort uses a nested loop. | Fine at the benchmarked scale (staff 366 days: 136 ms at 50k incidents) but grows with event volume. | Re-benchmark in Milestone 6 at production-like volume; options are a partial index for reopen events, extended statistics, or a periodically refreshed summary table. |
| ISSUE-14 | Analytics read the same primary database as the application. | Heavy dashboard use competes with incident writes. | Milestone 6: consider a read replica or response caching if load requires it. |
| ISSUE-15 | CSV formula-injection defence has spreadsheet-specific limits: the leading apostrophe is hidden by Excel but may appear as a visible character in other tools (some LibreOffice and Google Sheets import paths, text editors); it protects the exported file only, not data copied out of it or re-saved without the prefix; and it targets the OWASP trigger set (`= + - @`, tab, CR, LF and their full-width forms, also after leading whitespace). | A spreadsheet user could still be tricked if they remove the apostrophe or paste the cell elsewhere. | Document for users in Milestone 6; revisit if a target application with other triggers is adopted. |
| ISSUE-16 | CSV exports are capped per request (10,000 rows) and every request is recorded, but there is no per-user rate limit, alerting, or screen for reviewing `incident_exports`. | Repeated exports are detectable only by querying the database. | Milestone 6: export rate limiting and an admin view or alert for unusual export volume. |
| ISSUE-17 | The end-to-end suite runs in Chromium only, against the Vite development server, and does not cover API-unavailable states or exact theme colours (covered by frontend unit tests and screenshots instead). | Browser-specific or production-build regressions would not be caught end to end. | Milestone 6: run the suite against the production frontend image; consider Firefox and WebKit projects. |
| ~~ISSUE-18~~ | ~~Comments could not be redacted.~~ | — | **Fixed in Milestone 6 Phase B**: append-only administrative redaction; the text is withheld from every reader. The original remains in the database and backups (ISSUE-23). |
| ISSUE-19 | Comment posting is not rate limited, and comment IDs come from one sequence shared by public and internal comments, so a reporter could infer that other comments were created between two of theirs (not which incident, author or content). | Spam or flooding by an authenticated user; minor activity side channel. | Milestone 6: per-user rate limiting with the shared limiter (ISSUE-7); revisit IDs if the side channel matters for deployment. |
| ISSUE-20 | The CSV export ignores the list's SLA filter (it applies only severity and status); the export button's accessible name states its scope. | Exports from an SLA-filtered list contain more rows than shown. | Future: pass `sla` to the export with the same conditions and tests. |
| ISSUE-21 | Escalations are in-app only (badge, staff filter, dashboard count); nobody is notified by email or webhook. | Staff must look at the application to notice an escalation. | Milestone 6 (D12): notifications through a configured channel, without incident text in the message. |
| ISSUE-22 | The stored SLA read model is maintained by the application; a direct SQL change to incidents or events would bypass it. | Stored SLA figures could drift from the audit trail. | `python -m app.cli sla-verify` detects any difference; Milestone 6: run it on a schedule with alerting, and the least-privilege role (ISSUE-12) prevents direct changes. |
| ISSUE-23 | AI triage has no retention or redaction policy: with the real provider, incident text is sent to a third party, and stored suggestions are append-only. | **Production-readiness blocker for real data.** Synthetic data only. | Milestone 6: data-processing review, retention period, redaction (with ISSUE-18) and a documented provider data policy before any real data. |
| ISSUE-24 | The real provider has never been called: its behaviour is covered only by a stand-in SDK client, and the Haiku evaluation has not been run. | Real-model quality, latency and token counts are unknown. | Run `python -m app.triage_eval --provider anthropic` after separate approval of the spend (worst case $0.105 for three runs). |
| ~~ISSUE-25~~ | ~~Duplicate-candidate search took 180–394 ms for staff on the 52,000-incident benchmark.~~ | — | **Resolved before release** with the stored generated search vector (27–86 ms). |
| ISSUE-26 | Candidate search and the fake provider are English-only (English text-search configuration and keywords). | Non-English reports get fewer candidates and keyword-based test suggestions. | Evaluate the real model on the mixed-language cases; consider the `simple` configuration. |
| ISSUE-27 | Recorded costs are estimates (reported tokens × configured prices), not billing data; calls with unknown billing, including rate-limited ones, are charged their full reservation. | Budgets over-count, never under-count. | Reconcile with provider billing in Milestone 6 if needed. |
| ISSUE-28 | Triage settings are read at start-up, and the budget lock serialises all reservations across the application. | Changing limits needs a restart; reservation throughput is bounded (one short transaction at a time). | Acceptable at demonstration scale; revisit with shared rate limiting (ISSUE-7). |
| ISSUE-29 | Colour tokens use CSS `light-dark()` (Chrome and Edge 123+, Firefox 120+, Safari 17.5+). | Older browsers would show the pages without colours. | Acceptable for current browsers; add fallback values in Milestone 6 if older browsers must be supported. |
| ISSUE-30 | The theme choice is stored per browser only, and a page load shows the operating-system theme until the script runs (a brief flash when the stored choice differs). | Minor; no data involved. | Milestone 6: consider an inline, hashed theme script once the Content Security Policy exists. |
| ISSUE-31 | The public demo accounts are shared: every visitor signs in as the same reporter or responder, so visitors see each other's incidents, comments and status changes until the next reset. | A visitor could leave misleading or unpleasant synthetic content for up to a day. | Daily reset, per-IP write limits and administrative comment redaction; an administrator can reset now. |
| ISSUE-32 | During the daily reset (a few seconds at 04:00 UTC) requests wait for its table locks; one caught in a deadlock with the reset is aborted by PostgreSQL, and every session ends. | A visitor at that moment can see one failed request and must sign in again. | Accepted for a demo; the reset retries deadlocks itself and gives up after 30 seconds if blocked. |

---

## Remaining milestones

Milestones are delivered in order, each as a small, separately approved change with passing CI. **Each milestone keeps all earlier tests passing and adds tests for its own behaviour.** Milestones 2–4 must apply the same visibility rule as incident endpoints (reporters: own incidents only) and include negative tests for it.

### Approved sequence (2026-11-15)

| Order | Milestone | Status |
|---|---|---|
| 1–4 | Authentication/RBAC, audit trail, analytics dashboard, secure CSV export | **Complete** (see *Completed functionality*) |
| 5 | Permanent Playwright end-to-end tests in GitHub Actions | **Complete** (`13d3c34`, `7e65e81`, `dea4ba7`) |
| 5A | Incident comments and internal-only responder notes | **Complete** (`ddc571e`) |
| 5B | SLA tracking, response/resolution deadlines, overdue indicators and escalation | **Complete** (`d0ea3e8`) |
| 5C | AI-assisted incident triage with human approval | **Complete** (`4a216d7`) |
| 5D | Dashboard UI/UX redesign with a light/dark theme toggle | **Complete** (`e3bc126`) |
| 6 | Production hardening, observability, monitoring and deployment | In progress: Phase A complete (`5e4548e`); Phase B complete (`8ea9c72`); Phase C verified locally; Phases D–G planned |
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

### Milestone 5A — Incident comments and internal-only responder notes · Complete (`ddc571e`)

Discussion on an incident: public comments that the reporter can see, and internal notes visible only to responders and admins.

**Approved decisions (2026-11-15):** append-only comments with no edit or delete (D1); the `incident_comments` row is the audit record, separate from `incident_events` (D2); staff default to internal notes (D3); oldest first, 50 per page (D4); 5,000-character limit enforced identically by the API and PostgreSQL (D5); comments allowed on every status (D6); the incident's `updated_at` is unchanged by comments (D7); a browser-derived "Reporter" badge only (D8); no visibility filter (D9); no comments in the export or dashboard (D10); rate limiting deferred (D11). Implemented as described in *Comments and internal notes design*.

**Acceptance criteria**
- [x] Reporters can read and add public comments on their own incidents only; responders and admins can read and add both kinds on any incident. *(Verified locally; CI run 43749980036 passed.)*
- [x] Internal notes never reach a reporter through any path: comment list, incident detail, history, analytics, CSV export or error messages (negative tests per path, including end-to-end). *(Verified locally, including counts and `/auth/me`; CI run 43749980036 passed.)*
- [x] Author identity always from the session; bodies rendered as text; size limits enforced; CSRF on writes. *(Verified locally; CI run 43749980036 passed.)*
- [x] Comment creation (and any edit or delete allowed by the approved policy) is auditable with actor and time. *(Append-only rows; no edit or delete exists. Verified locally; CI run 43749980036 passed.)*
- [x] Backend, frontend and end-to-end tests, including cross-reporter isolation and deliberate-break checks. *(18 of 18 variants caught. Verified locally; CI run 43749980036 passed.)*

### Milestone 5B — SLA tracking, deadlines, overdue indicators and escalation · Complete (`d0ea3e8`)

Response and resolution targets per severity, visible deadlines, overdue status and escalation.

Implemented as described in *SLA design* (approved D1–D14, refinements 1–10 and the stored read model). The original anticipated additions, kept for reference:

**Anticipated technical additions (as planned)**
- SLA policy per severity (response target and resolution target), stored in a table or configuration and changeable only by admins; definitions of "responded" (for example first triage action by staff) and "resolved" reuse the audit trail, as the analytics do.
- Deadlines derived from `created_at` and the policy; overdue and breached states computed in SQL so list filters, dashboard figures and exports agree.
- Escalation when a deadline passes (for example raising an escalation level and notifying or highlighting for staff), recorded in the audit trail. This needs a system-actor design, because audit events currently require a human actor; the approach is decided in the plan.
- Overdue indicators in the list and detail page, an "overdue" filter, SLA compliance figures on the dashboard, and SLA columns in the CSV export.
- A testable clock (injectable "now") so deadline and escalation tests are deterministic.

**Acceptance criteria**
- [x] SLA targets per severity are documented, configurable by admins only, and changes are auditable. *(CI run 43766899182 passed.)*
- [x] Deadlines, overdue and breached states are correct at boundaries (exactly at the deadline, after a status change, after reopening) and computed in UTC with display in the viewer's time zone. *(CI run 43766899182 passed.)*
- [x] Escalation happens once per breach, is idempotent and is recorded with its cause. *(Including concurrency; CI run 43766899182 passed.)*
- [x] Visibility rules apply to every SLA figure, filter and export (cross-reporter tests). *(CI run 43766899182 passed.)*
- [x] Unit, API, end-to-end and deliberate-break tests; performance checked on the benchmark dataset. *(CI run 43766899182 passed.)*

### Milestone 5C — AI-assisted incident triage with human approval · Complete (`4a216d7`)

Suggested severity and triage notes for new incidents, which a responder reviews before anything changes.

Implemented as described in *AI triage design*; evidence under *Completed functionality* (Verified). The original anticipated additions, kept for reference:

**Anticipated technical additions (as planned)**
- A triage service that sends incident text to a language model and requests **structured output** validated against a strict schema (for example suggested severity from the existing enum, a short rationale, suggested next steps, possible duplicate incident IDs from the caller's visible set).
- Suggestions stored separately from the incident, with model identifier, prompt version, timestamps and validation result; nothing is applied automatically.
- A staff-only review panel: accept (applying changes through the normal, audited update path with the human as actor), edit, or reject.
- Provider API key supplied only through a secret store or environment variable, never the repository; the feature disabled when no key is configured; tests use a deterministic fake model.
- A small, synthetic, version-controlled evaluation dataset (for example 30–50 labelled incidents, including prompt-injection attempts) with a script reporting agreement with human labels, invalid-output rate and injection resistance.

**Security and acceptance criteria**
- [x] Incident text is treated as untrusted input: the model has no tools, database access or ability to act; output outside the schema or allowed values is rejected; prompt-injection cases in the evaluation set do not change behaviour beyond a schema-valid suggestion. *(CI run 43784327150 passed.)*
- [x] Human approval is required for every change; accepted suggestions are recorded in the audit trail with the approving user as actor and a reference to the suggestion. *(CI run 43784327150 passed.)*
- [x] Data minimisation: only fields needed for triage are sent; reporters cannot see staff-only data through suggestions; visibility rules apply to duplicate detection. *(CI run 43784327150 passed.)*
- [x] Timeouts, rate limits and a cost ceiling; failures degrade gracefully to manual triage. *(CI run 43784327150 passed.)*
- [x] Evaluation results recorded in this document; CI uses the fake model only (no secrets or paid calls in CI). *(Fake provider only; the paid Haiku evaluation awaits approval, ISSUE-24. CI run 43784327150 passed.)*

### Milestone 5D — Dashboard UI/UX redesign · Complete (`e3bc126`)

A modern, accessible dashboard on the existing analytics API, and a light/dark theme toggle. Implemented as described in *Dashboard and theme design*; evidence under *Completed functionality* (Verified).

**Acceptance criteria**
- [x] Five headline figures from real data, labelled by date range or current state; reporters see their own incidents only. *(CI run 43871416066 passed.)*
- [x] Presets, custom range, URL persistence, trend chart and table unchanged. *(CI run 43871416066 passed.)*
- [x] Trend, status donut and severity bars with labels, keyboard access and text alternatives; SLA rates, overdue counts and staff-only escalations. *(CI run 43871416066 passed.)*
- [x] Validated colours and zero serious or critical axe violations in both themes; theme choice persisted per browser. *(CI run 43871416066 passed.)*
- [x] No horizontal scrolling at 390, 768, 1,024 and 1,280 px; no backend, API or dependency changes; all suites pass in CI. *(CI run 43871416066 passed.)*

### Milestone 6 — Production hardening, observability, monitoring and deployment · In progress (Phase A complete, `5e4548e`)

The original hardening and deployment criteria below are unchanged; observability and monitoring are added. README improvements, screenshots and the architecture diagram move to the *Final presentation*.

**Acceptance criteria**
- [ ] Production frontend image: static build served by a minimal web server that also proxies `/api`; no dev server in production.
- [ ] Containers run as non-root users; minimal base images; health checks; migrations applied automatically and safely at deploy time.
- [ ] Security headers (Content-Security-Policy, HSTS when on HTTPS, `X-Content-Type-Options`, frame protections); CORS locked down; request size limits.
- [ ] Configuration only through environment variables, with a documented `.env.example`; no default credentials in production paths (resolves ISSUE-4).
- [ ] Shared, restart-safe login rate limiting with correct client IPs behind the proxy (ISSUE-7); expired-session cleanup (ISSUE-9); HTTPS-only cookies (ISSUE-11).
- [ ] **Production-readiness blocker:** administrative redaction of accidentally published sensitive comments, with an immutable redaction record and visibility tests (ISSUE-18); per-user rate limiting of comment posting (ISSUE-19).
- [ ] Least-privilege database role for the app (no ownership; `INSERT`/`SELECT` only on `incident_events`) (ISSUE-12); automatic unassignment on demotion or deactivation, recorded in the audit trail (ISSUE-10).
- [ ] Structured logging without secrets or personal data; consistent error responses.
- [ ] Dependency review and CI dependency/vulnerability scanning; resolve ISSUE-3.
- [ ] Documented deployment to a chosen host, with a deploy workflow that uses a protected environment for any secrets.
- [ ] Final documentation: README with architecture diagram, screenshots, setup, testing, security model and roles, API reference, and a short "design decisions" section. *(Delivered with the Final presentation.)*
- [ ] Observability: structured logs with request IDs (no secrets or personal data), liveness and readiness endpoints, and application metrics (request rate, latency and errors per route; database pool; export and analytics counts).
- [ ] Evaluate **Prometheus** (metrics endpoint and scraping), **Grafana** (provisioned dashboards) and **OpenTelemetry** (traces across FastAPI and SQLAlchemy); record the decision and adopt what fits the chosen hosting, with the reasons documented.
- [ ] Monitoring and alerting for availability, error rate and (after Milestone 5B) SLA breaches, with a short runbook; escalation notifications (ISSUE-21) and a scheduled `sla-verify` check (ISSUE-22); run the `sla-worker` service in the deployment.
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
