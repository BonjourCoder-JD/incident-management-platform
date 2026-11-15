# incident-management-platform

[![CI](https://github.com/BonjourCoder-JD/incident-management-platform/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/BonjourCoder-JD/incident-management-platform/actions/workflows/ci.yml)

Full-stack incident management application built with React, TypeScript, FastAPI, and PostgreSQL.

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
- Tamper-resistant audit trail: every change recorded with who, when and before/after values, in the same transaction, shown as a readable history
- User management for admins and self-service password changes
- Loading, empty, error, forbidden and success states throughout, and a responsive layout down to phone width

Setup, local test commands, the frontend guide and the API reference are in [README-foundation.md](README-foundation.md). Architecture, verified functionality, known issues and the remaining milestones are in [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md).

## Continuous integration

Every push and pull request to `main` runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

| Job | What it checks |
|---|---|
| Backend tests | Applies migrations to a disposable PostgreSQL database, checks models match migrations (`alembic check`), runs the pytest suite |
| Migration round trip | On a separate disposable database: upgrade, downgrade to base (confirming tables and enum types are removed), upgrade again, then run the admin bootstrap command |
| Frontend tests and build | `npm ci` from the lockfile, Vitest + React Testing Library tests, TypeScript type check and Vite production build |
| Docker images | Builds the backend and frontend images |

CI never connects to a development database and uses no repository secrets. The database password in the workflow is a throwaway value for the job's temporary container. The workflow token is read-only (`contents: read`).
