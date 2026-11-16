# incident-management-platform

[![CI](https://github.com/BonjourCoder-JD/incident-management-platform/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/BonjourCoder-JD/incident-management-platform/actions/workflows/ci.yml)

Full-stack incident management application built with React, TypeScript, FastAPI, and PostgreSQL.


## Engineering Case Study

[View Engineering Case Study (PDF)](docs/Case_Study.pdf)

A two-page case study covering the architecture, security engineering, automated testing, engineering challenges (including an SLA performance investigation on a 50,000-incident benchmark), and the planned final dashboard. The dashboard image is a design concept, not a screenshot of the current application; planned features are identified separately from completed work.

## Stack

- **Frontend:** React 19, TypeScript (strict), Vite
- **Backend:** FastAPI, SQLAlchemy 2, Alembic migrations
- **Database:** PostgreSQL 17
- **Tooling:** Docker Compose for local development, pytest, Vitest + React Testing Library, GitHub Actions

## Features

- Sign-in with server-side sessions (HttpOnly cookie, CSRF protection, Argon2id passwords, login rate limiting)
- Role-based access control enforced by the API: reporters see and edit only their own incidents; responders triage everything; admins also manage users and roles
- Incident list with severity and status filters and pagination
- Report-an-incident form with validation and clear error messages
- Incident detail view with one-click status changes, assignment and editing
- Secure CSV export: role-scoped, streamed, formula-injection safe, size-limited, and recorded in an append-only log
- Analytics dashboard computed in PostgreSQL: five headline figures labelled by date range or current state, a created-versus-resolved trend, a status donut, severity bars, SLA compliance and escalations, current backlog and time to first resolution, scoped to what each role may see, with keyboard-readable charts and table alternatives
- Tamper-resistant audit trail: every change recorded with who, when and before/after values, in the same transaction, shown as a readable history
- Incident discussion: public comments between the reporter and responders, plus internal notes that only responders and admins can read, enforced by the API; comments are append-only and shown as plain text; administrators can remove a comment's text, with the removal and its reason recorded
- SLA tracking: response and resolution targets per severity, deadlines and overdue states on every incident and in the list, dashboard compliance figures, automatic escalation by a background worker, and admin-only policy versions with history
- Optional AI-assisted triage (off by default): a suggested severity, rationale, next steps and possible duplicates that a responder reviews, edits and accepts or rejects; nothing changes without a person, every decision is audited, and spending is capped in the database. Tests and CI use an offline test provider only
- User management for admins and self-service password changes
- Light and dark themes that follow the operating system until you choose one in the header (remembered in that browser)
- Loading, empty, error, forbidden and success states throughout, and a responsive layout down to phone width

Setup, local test commands, the frontend guide and the API reference are in [README-foundation.md](README-foundation.md). Architecture, verified functionality, known issues and the remaining milestones are in [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md).

## Production build (in progress)

Milestone 6 prepares a public demo with synthetic data only; **nothing is deployed yet**. [`deploy/compose.prod.yaml`](deploy/compose.prod.yaml) runs PostgreSQL, a one-off migration step, the API and the SLA worker as non-root users, and Caddy, which serves the built React app over HTTPS with strict security headers and forwards `/api` to the backend. The backend reports `/health/ready` as 503 until the database schema matches the code, and refuses to start with unsafe production settings (for example a weak database password or a paid AI provider). In production the API and worker connect as a least-privilege database role that can read and add audit records but never change, remove or unprotect them; sign-in failures and public-demo writes are rate limited in PostgreSQL (shared by every process and kept across restarts); and administrators can remove a comment's text with a recorded reason. With `DEMO_MODE=true` it becomes a public demo: the sign-in page publishes a reporter and a responder account (the administrator stays private), a banner explains that all data is synthetic and AI triage is simulated, the demo accounts cannot be locked out by email or changed, and a reset service recreates the same synthetic scenarios every day at 04:00 UTC, refusing any database whose name does not end in `_demo`. Settings are listed in [`deploy/.env.production.example`](deploy/.env.production.example); the remaining work before going public is tracked in [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md).

## Continuous integration

Every push and pull request to `main` runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

| Job | What it checks |
|---|---|
| Backend tests | Applies migrations to a disposable PostgreSQL database, checks models match migrations (`alembic check`), runs the pytest suite |
| Migration round trip | On a separate disposable database: upgrade, downgrade to base (confirming tables and enum types are removed), upgrade again, then run the admin bootstrap command |
| Frontend tests and build | `npm ci` from the lockfile, Vitest + React Testing Library tests, TypeScript type check and Vite production build |
| Docker images | Builds the backend and frontend images |
| End-to-end tests | Playwright (Chromium) against a disposable Docker Compose stack with random per-run passwords: sign-in and sessions, authorization and cross-reporter isolation, incidents, history, dashboard, CSV export, triage with the offline test provider and axe accessibility checks; then a public-demo run against the production images (`run.sh --demo`). Failure evidence is limited to screenshots, page snapshots and backend logs |

CI never connects to a development database and uses no repository secrets. The database password in the workflow is a throwaway value for the job's temporary container. The workflow token is read-only (`contents: read`).
