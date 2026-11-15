# Security Incident Management Platform — Foundation

React/TypeScript frontend, FastAPI backend, PostgreSQL, Docker Compose.

## Run locally

1. Copy `.env.example` to `.env` and set a unique local password.
2. Run `docker compose up --build -d`.
3. Open http://localhost:5173 and check API status `Connected`.
4. Open http://localhost:8000/docs for Swagger UI.
5. Test http://localhost:8000/health and http://localhost:8000/health/db.

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

The tests use the Compose PostgreSQL database. Each test runs in a transaction that is rolled back, so no test records are kept.

## Incident API

| Method | Path | Description |
|---|---|---|
| `POST` | `/incidents` | Create an incident (`title`, `description`, `severity`, optional `assignee_id`) |
| `GET` | `/incidents` | List incidents, newest first. Query: `limit` (1–100, default 20), `offset` (≥0), `severity`, `status` |
| `GET` | `/incidents/{incident_id}` | Get one incident |
| `PATCH` | `/incidents/{incident_id}` | Update `title`, `description`, `severity`, `status` or `assignee_id` |

Severity: `low`, `medium`, `high`, `critical`. Status: `open`, `investigating`, `resolved`, `closed`.

## Notes

This is a foundation milestone, not a production deployment. Authentication, authorization, incident workflows, migrations, tests and CI will follow. Never commit `.env` or real credentials.
