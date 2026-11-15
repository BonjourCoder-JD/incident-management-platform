#!/usr/bin/env bash
# Runs the end-to-end suite against a disposable Docker Compose stack.
#
#   e2e/run.sh [playwright args]              Playwright on this machine (Node 24, `npx playwright install chromium`)
#   e2e/run.sh --in-docker [playwright args]  Playwright inside the official Playwright image (no local Node needed)
#
# The stack is always the Compose project "imp-e2e": its own containers, network and database volume
# (imp-e2e_postgres_data). Passwords are random per run and never written to disk. The stack is removed
# at the end, but only after checking that every volume to be removed belongs to that project.
set -euo pipefail

cd "$(dirname "$0")/.."  # repository root

readonly PROJECT="imp-e2e"
readonly PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright:v1.64.1-noble"
COMPOSE=(docker compose -p "$PROJECT" -f compose.yaml -f e2e/compose.e2e.yaml)

MODE="host"
if [[ "${1:-}" == "--in-docker" ]]; then
  MODE="docker"
  shift
fi

host_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# Safety check before any destructive cleanup: only volumes Docker labels as part of this project, and
# only ones named after it, may be removed. Development volumes belong to a different project.
assert_e2e_only() {
  [[ "$PROJECT" == "imp-e2e" ]] || { echo "Refusing to clean up: unexpected project '$PROJECT'." >&2; exit 1; }
  local volume
  for volume in $(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT"); do
    if [[ "$volume" != "${PROJECT}_"* ]]; then
      echo "Refusing to clean up: volume '$volume' is not an end-to-end volume." >&2
      exit 1
    fi
  done
}

remove_stack() {
  assert_e2e_only
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}

on_exit() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    mkdir -p e2e/test-results
    "${COMPOSE[@]}" logs --no-color backend > e2e/test-results/backend.log 2>&1 || true
  fi
  remove_stack
  exit "$status"
}

# Per-run secrets. Exported variables take precedence over a developer's .env file.
export POSTGRES_USER="incident"
export POSTGRES_DB="incidents"
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
E2E_ADMIN_PASSWORD="$(openssl rand -hex 24)"
export POSTGRES_PASSWORD E2E_ADMIN_PASSWORD
export BOOTSTRAP_ADMIN_PASSWORD="$E2E_ADMIN_PASSWORD"
export E2E_ADMIN_EMAIL="e2e.admin@example.com"
export E2E_RUN_ID="${E2E_RUN_ID:-$(openssl rand -hex 3)}"
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "::add-mask::$POSTGRES_PASSWORD"
  echo "::add-mask::$E2E_ADMIN_PASSWORD"
fi

trap on_exit EXIT
remove_stack  # leftovers from an interrupted earlier run

echo "Starting the disposable stack (project $PROJECT)…"
"${COMPOSE[@]}" up -d --build --wait
"${COMPOSE[@]}" exec -T backend alembic upgrade head
"${COMPOSE[@]}" exec -T -e BOOTSTRAP_ADMIN_PASSWORD backend python -m app.cli create-admin --email "$E2E_ADMIN_EMAIL"

# Two synthetic incidents created "before audit logging" (no `created` event), for legacy-data tests.
legacy_ids="$("${COMPOSE[@]}" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c \
  "INSERT INTO incidents (title, description, severity) VALUES
     ('E2E legacy incident (read-only)', 'Synthetic incident created before audit logging.', 'low'),
     ('E2E legacy incident (resolvable)', 'Synthetic incident created before audit logging.', 'high')
   RETURNING id" | grep -E '^[0-9]+$')"
E2E_LEGACY_READONLY_ID="$(sed -n 1p <<<"$legacy_ids")"
E2E_LEGACY_RESOLVE_ID="$(sed -n 2p <<<"$legacy_ids")"
export E2E_LEGACY_READONLY_ID E2E_LEGACY_RESOLVE_ID

echo "Waiting for the frontend and API…"
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T frontend wget -qO- http://localhost:5173/api/health 2>/dev/null | grep -q '"ok"'; then
    break
  fi
  sleep 1
done

echo "Running Playwright ($MODE)…"
if [[ "$MODE" == "host" ]]; then
  (cd e2e && E2E_BASE_URL="http://localhost:15173" npx playwright test "$@")
else
  MSYS_NO_PATHCONV=1 docker run --rm --init \
    --network "container:${PROJECT}-frontend-1" \
    -e CI -e E2E_ADMIN_EMAIL -e E2E_ADMIN_PASSWORD -e E2E_RUN_ID \
    -e E2E_LEGACY_READONLY_ID -e E2E_LEGACY_RESOLVE_ID -e E2E_BASE_URL="http://localhost:5173" \
    -v "$(host_path "$PWD/e2e"):/e2e" -w /e2e "$PLAYWRIGHT_IMAGE" \
    bash -c 'npm ci --no-audit --no-fund >/dev/null && npx playwright test "$@"' playwright "$@"
fi
