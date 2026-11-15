# Security Incident Management Platform — Foundation

React/TypeScript frontend, FastAPI backend, PostgreSQL, Docker Compose.

## Run locally

1. Copy `.env.example` to `.env` and set a unique local password.
2. Run `docker compose up --build -d`.
3. Apply database migrations: `docker compose run --rm backend alembic upgrade head`.
4. Create the first admin account (see [Accounts and sign-in](#accounts-and-sign-in)).
5. Open http://localhost:5173, sign in, and check that the header shows `API connected`.
6. Open http://localhost:8000/docs for Swagger UI.
7. Test http://localhost:8000/health and http://localhost:8000/health/db.

Stop with `docker compose down` (preserves the database volume).

## Database, migrations, tests and API

Run these from the repository root. The backend image contains the application code, tests and Alembic migrations, so rebuild it after code changes.

```sh
# 1. Start PostgreSQL (waits for the healthcheck)
docker compose up -d --wait db

# 2. Build the backend image
docker compose build backend

# 3. Apply database migrations
docker compose run --rm backend alembic upgrade head

# 4. Run the automated test suite
docker compose run --rm backend python -m pytest -v

# 5. Start the API (and frontend)
docker compose up --build -d backend frontend
```

The tests use the Compose PostgreSQL database. Each test runs in a transaction that is rolled back, so no test records or test accounts are kept. CI runs the same suite against a disposable database.

## End-to-end tests

The Playwright suite in `e2e/` runs against a **disposable** stack: Compose project `imp-e2e` with its own database volume, the frontend on `127.0.0.1:15173`, the API unpublished, and random passwords generated for every run. It never touches the development database, and the stack is removed afterwards (the script first checks that every volume it removes belongs to `imp-e2e`).

```sh
# With Node.js 24 installed locally
cd e2e && npm ci && npx playwright install chromium && cd ..
bash e2e/run.sh

# Without a local Node.js install: run Playwright inside its official Docker image
bash e2e/run.sh --in-docker

# Extra arguments go to Playwright, for example a single file
bash e2e/run.sh --in-docker tests/export.spec.ts
```

A local run takes about 3 minutes (stack build included). Failure screenshots, page snapshots and traces stay in `e2e/test-results/` (git-ignored); the HTML report is written to `e2e/playwright-report/`. In CI, only screenshots, page snapshots and backend logs are uploaded on failure.

## Accounts and sign-in

There is no public sign-up. Create the first admin from the command line, then create everyone else from the **Users** page in the web interface.

```sh
# Prompts for the password without echoing it (needs an interactive terminal)
docker compose run --rm -it backend python -m app.cli create-admin --email admin@example.com

# Non-interactive alternative: pass the password through the environment, never as an argument
export BOOTSTRAP_ADMIN_PASSWORD   # set it first, e.g. with: read -rs BOOTSTRAP_ADMIN_PASSWORD
docker compose run --rm -e BOOTSTRAP_ADMIN_PASSWORD backend python -m app.cli create-admin --email admin@example.com
```

Use your own email address and a unique passphrase of at least 12 characters. The command refuses to overwrite an existing account and never prints the password.

| Role | Can |
|---|---|
| Reporter | Create incidents, view and edit the details of **their own** incidents. Cannot change status or assignment. |
| Responder | View, edit, assign and change the status of **all** incidents. |
| Admin | Everything a responder can, plus create users, change roles and deactivate accounts. |

Every rule is enforced by the API; the interface only hides what a role cannot use. Sessions use an HttpOnly cookie plus a CSRF token, and signing in is rate-limited. See [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md#authentication-and-session-design) for the full security design.

Optional backend settings (environment variables): `EXPORT_MAX_ROWS` (default 10,000), `SESSION_ABSOLUTE_HOURS` (default 12), `SESSION_IDLE_MINUTES` (default 120), `LOGIN_MAX_FAILURES_PER_EMAIL` (default 5), `LOGIN_MAX_FAILURES_PER_IP` (default 30), `LOGIN_FAILURE_WINDOW_SECONDS` (default 900), and `SESSION_COOKIE_SECURE` (default `true`; browsers accept secure cookies on `http://localhost`, so only set `false` for other plain-HTTP test hosts).

**Audit trail:** every incident creation and change is recorded with the signed-in user, the time and the before/after values, in the same transaction as the change. Records cannot be edited or deleted through the app, and a database trigger blocks `UPDATE`, `DELETE` and `TRUNCATE` on them (see the limits in [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md#audit-trail-design-milestone-2)). History starts when migration `0003` is applied; earlier changes are not backfilled.

**Upgrading an existing database:** migration `0002` keeps every incident. Incidents created before accounts existed have no reporter and are visible to responders and admins only. Their old numeric assignee IDs are cleared, because no users existed for them to refer to.

## Frontend

The web interface at http://localhost:5173 asks you to sign in, then lets you:

- **Browse incidents** (reporters see only their own, under "My incidents") newest first, filter by severity and status, and page through results (10 per page). Filters and the page number are kept in the URL, so filtered views can be bookmarked and the browser Back button works.
- **Report an incident** at `/incidents/new`, with inline validation that mirrors the API rules (required title up to 200 characters, required description and severity). Responders and admins can also choose an assignee.
- **Open an incident** at `/incidents/{id}` to edit its title, description and severity. Responders and admins can also change its status in one click and reassign it. Only changed fields are sent.
- **Export to CSV** from the incident list: "Export CSV" downloads the incidents you may see with the current filters (up to 10,000 rows; larger exports are refused, never cut short). Text that could start a spreadsheet formula is prefixed with an apostrophe, and every export request is recorded.
- **See the dashboard** at `/dashboard`: incidents created, still open and resolved in a chosen date range (last 7/30/90 days, 12 months or custom, in your browser's time zone), breakdowns by status and severity, the current open backlog, a created-versus-resolved trend (with a table view), and time to first resolution measured from the audit trail. Reporters see figures for their own incidents only.
- **Review its history** on the same page: who created it, and every later change to the title, description, severity, status or assignee, newest first, with long description changes collapsed. Reporters see the history of their own incidents only.
- **Manage users** at `/users` (admins only): create accounts, change roles, deactivate and reactivate.
- **Change your password** at `/account`, which signs out your other devices.

The frontend calls the API through the Vite dev server proxy: requests to `/api/*` are forwarded to the backend with the `/api` prefix removed, so the session cookie stays same-origin. No API URL or credentials are built into the frontend, and nothing is stored in `localStorage`.

### Frontend tests and build

The tests use Vitest, React Testing Library and jsdom, with the API mocked, so they need neither the backend nor the database.

```sh
# Run the frontend tests inside the frontend image
docker compose build frontend
docker compose run --rm --no-deps frontend npm test

# Type check and production build
docker compose run --rm --no-deps frontend npm run build
```

With Node.js 24 installed locally, the same commands work from `frontend/` after `npm ci`: `npm test`, `npm run test:watch`, `npm run build`.

## Incident API

All incident and user endpoints require a signed-in session (401 otherwise), and `POST`/`PUT`/`PATCH` requests also need the `X-CSRF-Token` header returned by `/auth/login` and `/auth/me`. Role rules are listed under [Accounts and sign-in](#accounts-and-sign-in); the full API, including `/auth/*` and `/users*`, is in [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md#api).

| Method | Path | Description |
|---|---|---|
| `POST` | `/incidents` | Create an incident (`title`, `description`, `severity`; `assignee_id` for responders and admins). The reporter is the signed-in user. |
| `GET` | `/incidents` | List visible incidents, newest first. Query: `limit` (1–100, default 20), `offset` (≥0), `severity`, `status` |
| `GET` | `/incidents/{incident_id}` | Get one incident (404 if it does not exist or is not visible to you) |
| `PATCH` | `/incidents/{incident_id}` | Update `title`, `description`, `severity`; responders and admins also `status` and `assignee_id` |
| `GET` | `/exports/incidents.csv` | CSV of the incidents you may see, with optional `severity`/`status`; 413 above `EXPORT_MAX_ROWS` (default 10,000). Read-only; each request is recorded. |
| `GET` | `/analytics/summary` | Dashboard figures for `from`–`to` (inclusive dates, at most 366 days) in time zone `tz`; reporters get their own incidents only. Read-only. |
| `GET` | `/incidents/{incident_id}/events` | Audit history, newest first: `limit` (1–100, default 20), `offset`. Same visibility as the incident. Read-only. |
| `GET` | `/incidents/{incident_id}/comments` | Discussion, oldest first: `limit` (1–100, default 50), `offset`. Reporters (own incidents only) get public comments only, and `total` counts only those. |
| `POST` | `/incidents/{incident_id}/comments` | Add `{body, visibility}`: `visibility` is required, `public` or `internal`; reporters may post `public` only (403). `body` is 1–5,000 characters after trimming, without control characters. Comments cannot be edited or deleted. |

Severity: `low`, `medium`, `high`, `critical`. Status: `open`, `investigating`, `resolved`, `closed`. `assignee_id` must be an active responder or admin (1–2147483647).

## Notes

This is not yet a production deployment. Authentication with role-based access control, database migrations, backend and frontend tests, and CI are in place. Production hardening (HTTPS, a production web server, shared rate limiting, non-root containers) is a later milestone, so keep the stack bound to `127.0.0.1` as configured. Never commit `.env`, real credentials or real user data.
