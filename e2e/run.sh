#!/usr/bin/env bash
# Runs the end-to-end suite against a disposable Docker Compose stack.
#
#   e2e/run.sh [playwright args]              Playwright on this machine (Node 24, `npx playwright install chromium`)
#   e2e/run.sh --in-docker [playwright args]  Playwright inside the official Playwright image (no local Node needed)
#   e2e/run.sh --demo [...]                   The public demo: production images in demo mode, database
#                                             incidents_demo seeded by the real daily reset, tests/demo.spec.ts,
#                                             then the backup and restore drill (e2e/backup_check.sh)
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
DEMO=false
while [[ "${1:-}" == "--in-docker" || "${1:-}" == "--demo" ]]; do
  if [[ "$1" == "--in-docker" ]]; then MODE="docker"; else DEMO=true; fi
  shift
done
if [[ "$DEMO" == true ]]; then
  COMPOSE+=(-f e2e/compose.e2e-demo.yaml)
  [[ $# -eq 0 ]] && set -- tests/demo.spec.ts
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
  "${COMPOSE[@]}" --profile backup down --volumes --remove-orphans >/dev/null 2>&1 || true
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
[[ "$DEMO" == true ]] && export POSTGRES_DB="incidents_demo"  # the reset only accepts names ending in _demo
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
E2E_ADMIN_PASSWORD="$(openssl rand -hex 24)"
E2E_SLA_REPORTER_PASSWORD="$(openssl rand -hex 24)"
E2E_APP_DB_PASSWORD="$(openssl rand -hex 24)"  # the runtime role (demo mode)
export POSTGRES_PASSWORD E2E_ADMIN_PASSWORD E2E_APP_DB_PASSWORD
export E2E_DEMO="$([[ "$DEMO" == true ]] && echo 1 || echo "")"
export BOOTSTRAP_ADMIN_PASSWORD="$E2E_ADMIN_PASSWORD"
export E2E_ADMIN_EMAIL="e2e.admin@example.com"
export E2E_RUN_ID="${E2E_RUN_ID:-$(openssl rand -hex 3)}"
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "::add-mask::$POSTGRES_PASSWORD"
  echo "::add-mask::$E2E_ADMIN_PASSWORD"
  echo "::add-mask::$E2E_SLA_REPORTER_PASSWORD"
  echo "::add-mask::$E2E_APP_DB_PASSWORD"
fi

trap on_exit EXIT
remove_stack  # leftovers from an interrupted earlier run

echo "Starting the disposable stack (project $PROJECT)…"
"${COMPOSE[@]}" up -d --build --wait
if [[ "$DEMO" == true ]]; then
  # As in production: migrations and the runtime role as the owner, the admin through the API image
  # (runtime role), then the real daily reset seeds the synthetic demo data.
  owner_url="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}"
  "${COMPOSE[@]}" exec -T -e DATABASE_URL="$owner_url" backend sh -c "alembic upgrade head && python -m app.cli db-roles"
  "${COMPOSE[@]}" exec -T -e BOOTSTRAP_ADMIN_PASSWORD backend python -m app.cli create-admin --email "$E2E_ADMIN_EMAIL"
  "${COMPOSE[@]}" exec -T -e DATABASE_URL="$owner_url" -e DEMO_MODE=true -e DEMO_DATABASE="$POSTGRES_DB" backend python -m app.cli demo-reset
else
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

# Synthetic SLA scenarios with back-dated audit events (Milestone 5B), then the real escalation
# command twice: the tests check that the first run recorded exactly one escalation and the second none.
export E2E_SLA_REPORTER_PASSWORD
eval "$("${COMPOSE[@]}" exec -T -e E2E_SLA_REPORTER_PASSWORD backend python - < e2e/seed_sla.py)"
escalation_counts=""
for _ in 1 2; do
  run_output="$("${COMPOSE[@]}" exec -T backend python -m app.cli sla-escalate 2>/dev/null)"
  echo "$run_output"
  escalation_counts+="$(grep -oE '[0-9]+ recorded' <<<"$run_output" | grep -oE '^[0-9]+') "
done
read -r E2E_SLA_FIRST_RUN_RECORDED E2E_SLA_SECOND_RUN_RECORDED <<<"$escalation_counts"
export E2E_SLA_REPORTER_EMAIL E2E_SLA_OVERDUE_ID E2E_SLA_APPROACHING_ID E2E_SLA_MET_ID E2E_SLA_BREACHED_ID E2E_SLA_REOPENED_ID
export E2E_SLA_FIRST_RUN_RECORDED E2E_SLA_SECOND_RUN_RECORDED
"${COMPOSE[@]}" exec -T backend python -m app.cli sla-verify  # stored SLA cycles match the audit trail
fi

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
    -e CI -e E2E_ADMIN_EMAIL -e E2E_ADMIN_PASSWORD -e E2E_RUN_ID -e E2E_DEMO \
    -e E2E_SLA_REPORTER_EMAIL -e E2E_SLA_REPORTER_PASSWORD -e E2E_SLA_OVERDUE_ID -e E2E_SLA_APPROACHING_ID -e E2E_SLA_MET_ID \
    -e E2E_SLA_BREACHED_ID -e E2E_SLA_REOPENED_ID -e E2E_SLA_FIRST_RUN_RECORDED -e E2E_SLA_SECOND_RUN_RECORDED \
    -e E2E_LEGACY_READONLY_ID -e E2E_LEGACY_RESOLVE_ID -e E2E_BASE_URL="http://localhost:5173" \
    -v "$(host_path "$PWD/e2e"):/e2e" -w /e2e "$PLAYWRIGHT_IMAGE" \
    bash -c 'npm ci --no-audit --no-fund >/dev/null && npx playwright test "$@"' playwright "$@"
fi

# Everything the suite did through the UI and API must still match the audit trail.
"${COMPOSE[@]}" exec -T backend python -m app.cli sla-verify

if [[ "$DEMO" == true ]]; then
  source e2e/backup_check.sh
  backup_check
fi
