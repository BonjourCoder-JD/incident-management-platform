#!/bin/sh
# Restore a backup into a NEW database (Milestone 6 Phase D). It never writes to an existing database.
#
#   restore.sh FILE [TARGET]
#
# FILE is a dump in BACKUP_DIR, for example incidents_demo-20261116T033000Z.dump. TARGET is the new
# database, by default <source>_restore_<UTC time>; it must be named *_restore_<suffix> and must not
# exist, so the live, development and test databases can never be targets.
# Steps: compare the SHA-256 file; create TARGET; restore in one transaction, stopping at the first
# error, without owners or privileges (the migrate service's db-roles grants the runtime role); if
# anything fails, remove the database this run created. Prints "restored_database=<name>" on success.
# Next: `python -m app.cli verify-restore` against TARGET, then the switch-over in docs/OPERATIONS.md.
set -eu
umask 077

BACKUP_DIR="${BACKUP_DIR:-/backups}"

log() {
  message=$(printf '%s' "$2" | tr -d '"\\' | tr '\n\r\t' '   ')
  printf '{"time":"%s","level":"%s","logger":"restore","message":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$message" >&2
}
fail() { log ERROR "$1"; exit "${2:-1}"; }

[ $# -ge 1 ] && [ $# -le 2 ] || fail "usage: restore.sh FILE [TARGET]" 2
file=$1
printf '%s' "$file" | grep -Eq '^[a-z0-9_]+-[0-9]{8}T[0-9]{6}Z\.dump$' || fail "not a backup file name: $file" 2
if [ ! -f "$BACKUP_DIR/$file" ] || [ ! -f "$BACKUP_DIR/$file.sha256" ]; then fail "backup or its checksum file is missing: $file" 2; fi
expected=$(cut -d ' ' -f 1 <"$BACKUP_DIR/$file.sha256")
actual=$(sha256sum "$BACKUP_DIR/$file" | cut -d ' ' -f 1)
[ -n "$expected" ] && [ "$expected" = "$actual" ] || fail "checksum mismatch: $file is damaged; nothing was restored" 1

source=${file%%-*}
target=${2:-${source}_restore_$(date -u +%Y%m%d%H%M%S)}
printf '%s' "$target" | grep -Eq '^[a-z][a-z0-9_]{0,40}_restore_[a-z0-9_]{1,20}$' ||
  fail "the target must be a new database named *_restore_<suffix>, not $target" 2
exists=$(psql --no-password --dbname=postgres --tuples-only --no-align -v ON_ERROR_STOP=1 \
  -c "SELECT count(*) FROM pg_database WHERE datname = '$target'") || fail "cannot reach the database server" 1
[ "$exists" = 0 ] || fail "database $target already exists; restore never writes to an existing database" 2

createdb --no-password "$target" || fail "cannot create the database $target" 1
errors=$(mktemp)
if pg_restore --no-password --exit-on-error --single-transaction --no-owner --no-privileges \
  --dbname="$target" "$BACKUP_DIR/$file" 2>"$errors"; then
  rm -f "$errors"
  log INFO "Restored $file into the new database $target"
  echo "restored_database=$target"
  exit 0
fi
log ERROR "Restore of $file failed: $(head -n 3 "$errors")"
rm -f "$errors"
dropdb --no-password "$target" && log INFO "Removed the incomplete database $target (created by this run)"
exit 1
