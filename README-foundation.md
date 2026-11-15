# Security Incident Management Platform — Foundation

React/TypeScript frontend, FastAPI backend, PostgreSQL, Docker Compose.

## Run locally

1. Copy `.env.example` to `.env` and set a unique local password.
2. Run `docker compose up --build -d`.
3. Open http://localhost:5173 and check API status `Connected`.
4. Open http://localhost:8000/docs for Swagger UI.
5. Test http://localhost:8000/health and http://localhost:8000/health/db.

Stop with `docker compose down` (preserves the database volume).

## Notes

This is a foundation milestone, not a production deployment. Authentication, authorization, incident workflows, migrations, tests and CI will follow. Never commit `.env` or real credentials.
