# Security Incident Management Platform — Foundation

React/TypeScript frontend, FastAPI backend, PostgreSQL, Docker Compose.

## Run locally

1. Copy `.env.example` to `.env` and set a unique local password.
2. Run `docker compose up --build -d`.
3. Apply database migrations: `docker compose run --rm backend alembic upgrade head`.
4. Open http://localhost:5173. The header should show `API connected`.
5. Open http://localhost:8000/docs for Swagger UI.
6. Test http://localhost:8000/health and http://localhost:8000/health/db.

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

## Frontend

The web interface at http://localhost:5173 lets you:

- **Browse incidents** newest first, filter by severity and status, and page through results (10 per page). Filters and the page number are kept in the URL, so filtered views can be bookmarked and the browser Back button works.
- **Report an incident** at `/incidents/new`, with inline validation that mirrors the API rules (required title up to 200 characters, required description and severity, optional positive whole-number assignee ID).
- **Open an incident** at `/incidents/{id}` to change its status in one click or edit its title, description, severity and assignee. Only changed fields are sent.

The frontend calls the API through the Vite dev server proxy: requests to `/api/*` are forwarded to the backend with the `/api` prefix removed. No API URL or credentials are built into the frontend.

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

| Method | Path | Description |
|---|---|---|
| `POST` | `/incidents` | Create an incident (`title`, `description`, `severity`, optional `assignee_id`) |
| `GET` | `/incidents` | List incidents, newest first. Query: `limit` (1–100, default 20), `offset` (≥0), `severity`, `status` |
| `GET` | `/incidents/{incident_id}` | Get one incident |
| `PATCH` | `/incidents/{incident_id}` | Update `title`, `description`, `severity`, `status` or `assignee_id` |

Severity: `low`, `medium`, `high`, `critical`. Status: `open`, `investigating`, `resolved`, `closed`.

## Notes

This is not yet a production deployment. Database migrations, backend and frontend tests, and CI are in place. **There is no authentication or authorization yet**: anyone who can reach the API or the web interface can read and change every incident, so keep the stack bound to `127.0.0.1` as configured. Authentication and authorization are the next milestone. Never commit `.env` or real credentials.
