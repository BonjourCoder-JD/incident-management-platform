# Incident Management Platform

Full-stack incident management application built with React, TypeScript, FastAPI, and PostgreSQL.

## Engineering Case Study

- **[View Engineering Case Study (PDF)](https://github.com/BonjourCoder-JD/incident-management-platform/blob/main/docs/Case_Study.pdf)** — A two-page case study covering architecture, security engineering, automated testing, engineering challenges (including an SLA performance investigation on a 50,000-incident benchmark), and the planned final dashboard.
- *Note:* The dashboard image is a design concept, not a screenshot of the current application; planned features are clearly separated from completed work.

## Quick Start (Local)

```bash
docker compose up --build
```

For full setup instructions, local test commands, the frontend guide, and API reference, see [`README-foundation.md`](README-foundation.md). Architecture, verified functionality, known issues, and remaining milestones are tracked in [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md).

## Stack

- **Frontend:** React 19, TypeScript (strict), Vite
- **Backend:** FastAPI, SQLAlchemy 2, Alembic migrations
- **Database:** PostgreSQL 17
- **Tooling:** Docker Compose for local development, `pytest`, Vitest + React Testing Library, GitHub Actions

## Features

- **Authentication & Sessions:** Server-side sessions with `HttpOnly` cookies, CSRF protection, Argon2id password hashing, and login rate limiting.
- **Role-Based Access Control (RBAC):** API-enforced roles—reporters view and edit only their own incidents; responders triage incidents; admins also manage users and roles.
- **Incident Workflows:** Filterable and paginated incident list (by severity and status), validated incident reporting form with clear errors, and a detail view supporting one-click status updates, assignment, and editing.
- **Secure CSV Export:** Role-scoped, streamed, formula-injection-safe, and size-limited exports recorded in an append-only log.
- **PostgreSQL Analytics Dashboard:** Five headline figures scoped by date range or current state, created-versus-resolved trends, status donut, severity bars, SLA compliance and escalations, current backlog, and time to first resolution—scoped to role permissions with keyboard-readable charts and table alternatives.
- **Tamper-Resistant Audit Trail:** Every change is recorded with actor, timestamp, and before/after values inside the same transaction, displayed as a readable history.
- **Incident Discussion:** Public comments between reporters and responders plus responder/admin-only internal notes enforced by the API; comments are append-only plain text, and admins can redact comment text while recording the removal and reason.
- **SLA Tracking:** Per-severity response and resolution targets, deadlines and overdue indicators in lists and details, dashboard compliance metrics, automatic background-worker escalation, and versioned admin-only SLA policies with history.
- **Optional AI-Assisted Triage (Off by Default):** Suggests severity, rationale, next steps, and potential duplicates for a responder to review, edit, and accept or reject—requiring human confirmation, auditing every decision, capping spend in PostgreSQL, and using an offline test provider in tests and CI.
- **Administration & UX:** Admin user management, self-service password changes, OS-synced light/dark themes with browser override persistence, loading/empty/error/forbidden/success states, and responsive layout down to mobile widths.

## Production Build (In Progress)

Milestone 6 prepares a synthetic-data-only public demo (not yet deployed):

- **Hardened Containers & Routing:** `deploy/compose.prod.yaml` runs PostgreSQL, a one-off migration step, the API, and the SLA worker as non-root users, paired with Caddy serving the built React app over HTTPS with strict security headers and proxying `/api` to the backend.
- **Fail-Safe Startup & Least Privilege:** `/health/ready` returns `503` until the schema matches the code, and startup rejects unsafe production settings (such as weak database passwords or a paid AI provider). In production, the API and worker use a least-privilege database role that can read and insert audit records without modifying, deleting, or unprotecting them.
- **Rate Limiting & Moderation:** Sign-in failures and public-demo writes are rate-limited in PostgreSQL across processes and restarts, and admins can redact comment text with a recorded reason.
- **Demo Mode (`DEMO_MODE=true`):** Publishes reporter and responder demo accounts (keeping the admin account private), displays a synthetic-data and simulated-AI banner, protects demo accounts from email lockout or mutation, and runs a daily `04:00 UTC` reset service that refuses any database whose name does not end in `_demo`.
- **Observability & Backups:** Services emit structured JSON logs with request IDs (excluding passwords, tokens, and incident text); `docker compose ps` reports health across API, worker, demo reset, and backups; daily database dumps back up data, with verified restores into new databases documented in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

Configuration templates live in `deploy/.env.production.example`, and remaining pre-launch items are tracked in [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md).

## Continuous Integration

Every push and pull request to `main` runs `.github/workflows/ci.yml`:

| Job | What It Checks |
|---|---|
| **Backend tests** | Applies migrations to a disposable PostgreSQL instance, verifies models match migrations (`alembic check`), and runs `pytest`. |
| **Migration round trip** | On a separate disposable database: runs `upgrade` → `downgrade` to base (confirming tables and enum types are removed) → `upgrade` again, then runs the admin bootstrap command. |
| **Frontend tests and build** | Runs `npm ci` from the lockfile, Vitest + React Testing Library tests, TypeScript type checking, and the Vite production build. |
| **Docker images** | Builds the backend and frontend container images. |
| **End-to-end tests** | Runs Playwright (Chromium) against a disposable Docker Compose stack with random per-run passwords (auth/sessions, RBAC and cross-reporter isolation, incidents, history, dashboard, CSV export, offline-provider triage, and `axe` accessibility checks), followed by a public-demo run against production images (`run.sh --demo`). Failure artifacts are limited to screenshots, page snapshots, and backend logs. |

Additional workflows in Milestone 6 Phase E cover:

- **Security scans:** `pip-audit`, `npm audit`, and Trivy on every change and weekly.
- **Release images:** Built as native `linux/arm64` images after `main` CI passes, scanned, smoke-tested on the runner, and published to GitHub Container Registry.
- **Deploy:** Manual, environment-approved production deployment documented in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

Base images and GitHub Actions are pinned by digest and commit SHA. CI never connects to a development database, uses no repository secrets, uses a throwaway database password for temporary containers, and uses only the standard workflow permissions required by each job.
