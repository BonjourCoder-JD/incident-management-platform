#!/usr/bin/env bash
# Backup and restore drill (Milestone 6 Phase D), sourced by e2e/run.sh --demo after the Playwright run,
# against the disposable demo stack: the real deploy/backup.sh and deploy/restore.sh in the real
# backup image, the real verify-restore command in the production backend image.

backup_check() {
  local -a run=("${COMPOSE[@]}" --profile backup run --rm -T)  # options, then the service name
  local -a sql=("${COMPOSE[@]}" exec -T db psql -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 --tuples-only --no-align)
  local owner="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432"
  local out status file restored before databases
  export MSYS_NO_PATHCONV=1  # Git Bash on Windows must not rewrite the container paths below
  fail() { echo "Backup drill FAILED: $*" >&2; exit 1; }
  fingerprint() {
    "${sql[@]}" -d "$1" -c "SELECT concat_ws(' ', (SELECT count(*) FROM users), (SELECT count(*) FROM incidents),
      (SELECT count(*) FROM incident_events), (SELECT coalesce(max(id), 0) FROM incident_events),
      (SELECT count(*) FROM incident_comments), (SELECT count(*) FROM triage_suggestions),
      (SELECT md5(string_agg(id || title || status, ',' ORDER BY id)) FROM incidents))"
  }

  echo "Backup drill: building the backup image…"
  "${COMPOSE[@]}" --profile backup build -q backup
  before="$(fingerprint "$POSTGRES_DB")"

  echo "Backup drill: a backup, its files and permissions…"
  out="$("${run[@]}" backup sh -c 'backup.sh once && backup.sh health && stat -c "%a %u %n" /backups /backups/*')" || fail "backup.sh once"
  echo "$out"
  file="$(grep -oE "${POSTGRES_DB}-[0-9]{8}T[0-9]{6}Z\.dump" <<<"$out" | head -n 1)"
  [[ -n "$file" ]] || fail "no dump written"
  grep -qx "700 10003 /backups" <<<"$out" || fail "the backup directory is not 0700"
  grep -qx "600 10003 /backups/$file" <<<"$out" || fail "the dump is not 0600"
  grep -qx "600 10003 /backups/$file.sha256" <<<"$out" || fail "the checksum file is not 0600"
  ! grep -q "$POSTGRES_PASSWORD" <<<"$out" || fail "the database password appeared in the output"

  echo "Backup drill: a failed backup leaves nothing behind and is unhealthy…"
  out="$("${run[@]}" -e PGPASSWORD=wrong-canary-password backup sh -c \
    'backup.sh once; echo "backup=$?"; backup.sh health; echo "health=$?"; ls -a /backups' 2>&1)" || true
  echo "$out"
  grep -qx "backup=1" <<<"$out" && grep -qx "health=1" <<<"$out" || fail "a failed backup was not reported"
  grep -q '"level":"ERROR"' <<<"$out" || fail "no error was logged"
  ! grep -q "wrong-canary-password" <<<"$out" || fail "the password appeared in the output"
  ! grep -q "partial" <<<"$out" || fail "a partial file was left behind"
  [[ "$(grep -c '\.dump$' <<<"$out")" == 1 ]] || fail "the failed backup left a dump"

  echo "Backup drill: damaged, incomplete and unsafe restores are refused without creating a database…"
  databases="$("${sql[@]}" -d postgres -c "SELECT string_agg(datname, ' ' ORDER BY datname) FROM pg_database")"
  "${run[@]}" backup sh -c "set -e; cd /backups
    cp $file ${POSTGRES_DB}-20000101T000000Z.dump; printf x >> ${POSTGRES_DB}-20000101T000000Z.dump
    sed 's/$file/${POSTGRES_DB}-20000101T000000Z.dump/' $file.sha256 > ${POSTGRES_DB}-20000101T000000Z.dump.sha256
    head -c 4000 $file > ${POSTGRES_DB}-20000102T000000Z.dump
    sha256sum ${POSTGRES_DB}-20000102T000000Z.dump > ${POSTGRES_DB}-20000102T000000Z.dump.sha256
    cp $file ${POSTGRES_DB}-20000103T000000Z.dump" || fail "could not prepare the damaged copies"
  expect_refused() {  # expected-exit-status restore-arguments...
    local expected=$1
    shift
    status=0
    out="$("${run[@]}" backup restore.sh "$@" 2>&1)" || status=$?
    [[ "$status" == "$expected" ]] || fail "restore.sh $* exited $status, expected $expected: $out"
    echo "  refused ($status): restore.sh $* — $(grep -oE '"message":"[^"]*"' <<<"$out" | tail -n 1)"
  }
  expect_refused 1 "${POSTGRES_DB}-20000101T000000Z.dump"  # checksum mismatch
  expect_refused 1 "${POSTGRES_DB}-20000102T000000Z.dump"  # valid checksum, truncated archive: restore fails
  expect_refused 2 "${POSTGRES_DB}-20000103T000000Z.dump"  # no checksum file
  expect_refused 2 "../../etc/passwd"
  expect_refused 2 "$file" "$POSTGRES_DB"                  # the live database
  expect_refused 2 "$file" "incidents"                     # the development database name
  expect_refused 2 "$file" "incidents_test"
  [[ "$("${sql[@]}" -d postgres -c "SELECT string_agg(datname, ' ' ORDER BY datname) FROM pg_database")" == "$databases" ]] ||
    fail "a refused or failed restore left a database behind"

  echo "Backup drill: export for an off-server copy (the newest dump and its checksum; damaged dumps refused)…"
  out="$("${run[@]}" backup backup.sh export 2>/dev/null | tar -tf -)" || fail "backup.sh export"
  [[ "$out" == "$file"$'\n'"$file.sha256" ]] || fail "unexpected export contents: $out"
  status=0
  out="$("${run[@]}" backup backup.sh export "${POSTGRES_DB}-20000101T000000Z.dump" 2>/dev/null)" || status=$?
  [[ "$status" == 1 && -z "$out" ]] || fail "a damaged dump was exported (exit $status)"
  echo "  export: $file and its checksum; damaged dump refused (1)"

  echo "Backup drill: restore into a new database and verify it…"
  out="$("${run[@]}" backup restore.sh "$file")" || fail "restore.sh $file"
  restored="$(sed -n 's/^restored_database=//p' <<<"$out")"
  [[ "$restored" =~ ^${POSTGRES_DB}_restore_[0-9]{14}$ ]] || fail "unexpected restored database name '$restored'"
  expect_refused 2 "$file" "$restored"                     # never into an existing database
  "${COMPOSE[@]}" exec -T -e DATABASE_URL="$owner/$POSTGRES_DB" backend python -m app.cli verify-restore --database "$restored" || fail "verify-restore"
  [[ "$(fingerprint "$restored")" == "$before" ]] || fail "the restored data differs from the source"
  [[ "$(fingerprint "$POSTGRES_DB")" == "$before" ]] || fail "the source database changed"

  echo "Backup drill: after db-roles the runtime role can use the restored copy (as in the switch-over)…"
  "${COMPOSE[@]}" exec -T -e DATABASE_URL="$owner/$restored" -e APP_DB_PASSWORD="$E2E_APP_DB_PASSWORD" backend python -m app.cli db-roles
  "${COMPOSE[@]}" exec -T -e DATABASE_URL="postgresql+psycopg://imp_app:${E2E_APP_DB_PASSWORD}@db:5432/$restored" backend \
    python -c "from app.health import readiness; ok, reason = readiness(check_role=True); print('readiness as imp_app:', reason); raise SystemExit(0 if ok else 1)" ||
    fail "the restored copy is not ready for the runtime role"
  unset MSYS_NO_PATHCONV
  echo "Backup drill passed."
}
