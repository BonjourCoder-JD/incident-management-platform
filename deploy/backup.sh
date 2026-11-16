#!/bin/sh
# Backups of the designated database (Milestone 6 Phase D). Runs in the backup image as an
# unprivileged user and connects to the database server as the schema owner.
#
#   backup.sh once     one backup now (exit 0 written, 1 failed, 2 misconfigured)
#   backup.sh daily    a backup now unless one is under 24 h old, then every day at BACKUP_TIME_UTC
#   backup.sh health   Docker health check: the latest attempt succeeded within BACKUP_MAX_AGE
#   backup.sh export [FILE]  a tar of one dump (default: the newest) and its SHA-256 file on stdout,
#                      for an encrypted off-server copy (docs/OPERATIONS.md); checked before export
#
# The SHA-256 file detects accidental damage (a truncated copy, a bad disk). It is stored next to the
# dump, so it does NOT prove that nobody changed the archive on purpose: whoever can change the dump
# can change its checksum too. Off-server copies are encrypted with authenticated encryption instead.
#
# Credentials come only from the environment (PGHOST, PGUSER, PGPASSWORD): never on a command line,
# in a file or in the output. BACKUP_DIR is 0700 and every file in it 0600. A dump is written under a
# hidden temporary name, checked with pg_restore --list, given a SHA-256 file and only then renamed,
# so a failed or interrupted backup never leaves a file that looks complete. The newest BACKUP_KEEP
# dumps are kept. Dumps contain password hashes: they are as sensitive as the database itself.
set -eu
umask 077

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-7}"
BACKUP_TIME_UTC="${BACKUP_TIME_UTC:-03:30}"
BACKUP_MAX_AGE="${BACKUP_MAX_AGE:-93600}"  # 26 hours
STATUS_FILE="${STATUS_DIR:-/tmp/imp-status}/backup"

log() {  # LEVEL MESSAGE: one JSON line, like the application's logs
  message=$(printf '%s' "$2" | tr -d '"\\' | tr '\n\r\t' '   ')
  printf '{"time":"%s","level":"%s","logger":"backup","message":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$message"
}

status() {  # ok|failed [epoch]
  mkdir -p "$(dirname "$STATUS_FILE")"
  printf '%s %s\n' "$1" "${2:-$(date -u +%s)}" > "$STATUS_FILE"
}

config_ok() {
  case "${BACKUP_DATABASE:-}" in
    '' | *[!a-z0-9_]*) log ERROR "BACKUP_DATABASE must name the database (lowercase letters, digits and underscores)"; return 1 ;;
  esac
  case "$BACKUP_KEEP" in '' | *[!0-9]* | 0) log ERROR "BACKUP_KEEP must be a positive whole number"; return 1 ;; esac
  printf '%s' "$BACKUP_TIME_UTC" | grep -Eq '^([01][0-9]|2[0-3]):[0-5][0-9]$' || { log ERROR "BACKUP_TIME_UTC must be HH:MM"; return 1; }
  if [ -z "${PGPASSWORD:-}" ]; then log ERROR "PGPASSWORD is not set"; return 1; fi
  if [ ! -d "$BACKUP_DIR" ] || [ ! -w "$BACKUP_DIR" ]; then log ERROR "BACKUP_DIR $BACKUP_DIR is not a writable directory"; return 1; fi
}

prune() {
  # shellcheck disable=SC2010  # names are written only by this script and filtered by a strict pattern
  ls -1 "$BACKUP_DIR" | grep -E "^$BACKUP_DATABASE-[0-9]{8}T[0-9]{6}Z\.dump\$" | sort -r | tail -n +"$((BACKUP_KEEP + 1))" |
    while read -r old; do
      rm -f "$BACKUP_DIR/$old" "$BACKUP_DIR/$old.sha256"
      log INFO "Removed old backup $old"
    done
}

backup_once() {
  config_ok || return 2
  chmod 700 "$BACKUP_DIR"
  rm -f "$BACKUP_DIR"/.*.partial  # left by an interrupted earlier run
  name="$BACKUP_DATABASE-$(date -u +%Y%m%dT%H%M%SZ).dump"
  partial="$BACKUP_DIR/.$name.partial"
  errors=$(mktemp)
  if pg_dump --no-password --format=custom --dbname="$BACKUP_DATABASE" --file="$partial" 2>"$errors" &&
    pg_restore --list "$partial" >/dev/null 2>>"$errors"; then
    digest=$(sha256sum "$partial" | cut -d ' ' -f 1)
    printf '%s  %s\n' "$digest" "$name" >"$BACKUP_DIR/$name.sha256"
    chmod 600 "$partial" "$BACKUP_DIR/$name.sha256"
    mv "$partial" "$BACKUP_DIR/$name"
    rm -f "$errors"
    prune
    log INFO "Backup $name written ($(wc -c <"$BACKUP_DIR/$name" | tr -d ' ') bytes, sha256 $digest)"
    status ok
    return 0
  fi
  log ERROR "Backup of $BACKUP_DATABASE failed: $(head -n 3 "$errors")"
  rm -f "$partial" "$errors"
  status failed
  return 1
}

seconds_until() {  # HH:MM (UTC) -> seconds until its next occurrence
  now=$(date -u +%s)
  target=$(date -u -d "$1" +%s)
  [ "$target" -gt "$now" ] || target=$((target + 86400))
  echo $((target - now))
}

daily() {
  # Never exits: a failure is logged, marks the service unhealthy and is retried the next day.
  if ! config_ok; then
    status failed
    while true; do sleep 3600; done
  fi
  recent=$(find "$BACKUP_DIR" -maxdepth 1 -name "$BACKUP_DATABASE-*.dump" -mmin -1440 | sort -r | head -n 1)
  if [ -n "$recent" ]; then
    status ok "$(stat -c %Y "$recent")"
    log INFO "Recent backup found: $(basename "$recent")"
  else
    backup_once || true
  fi
  while true; do
    wait=$(seconds_until "$BACKUP_TIME_UTC")
    log INFO "Next backup in $wait s ($BACKUP_TIME_UTC UTC)"
    sleep "$wait"
    backup_once || true
  done
}

health() {
  if [ ! -f "$STATUS_FILE" ]; then echo "no backup attempt recorded yet"; return 1; fi
  read -r state at <"$STATUS_FILE"
  age=$(($(date -u +%s) - at))
  if [ "$state" != ok ]; then echo "the latest backup failed ${age} s ago (see the log)"; return 1; fi
  if [ "$age" -gt "$BACKUP_MAX_AGE" ]; then echo "no successful backup for ${age} s"; return 1; fi
  echo "ok: latest backup ${age} s ago"
}

export_backup() {  # [FILE]: the archive goes to stdout, so messages go to stderr
  # shellcheck disable=SC2010  # names are written only by this script and filtered by a strict pattern
  file=${1:-$(ls -1 "$BACKUP_DIR" | grep -E '^[a-z0-9_]+-[0-9]{8}T[0-9]{6}Z\.dump$' | sort | tail -n 1)}
  if ! printf '%s' "$file" | grep -Eq '^[a-z0-9_]+-[0-9]{8}T[0-9]{6}Z\.dump$'; then log ERROR "no backup to export" >&2; return 2; fi
  if [ ! -f "$BACKUP_DIR/$file" ] || [ ! -f "$BACKUP_DIR/$file.sha256" ]; then log ERROR "backup or its checksum file is missing: $file" >&2; return 2; fi
  if [ "$(cut -d ' ' -f 1 <"$BACKUP_DIR/$file.sha256")" != "$(sha256sum "$BACKUP_DIR/$file" | cut -d ' ' -f 1)" ]; then
    log ERROR "checksum mismatch: $file is damaged; not exported" >&2
    return 1
  fi
  log INFO "Exporting $file" >&2
  tar -C "$BACKUP_DIR" -cf - "$file" "$file.sha256"
}

case "${1:-}" in
  once) backup_once ;;
  daily) daily ;;
  health) health ;;
  export) export_backup "${2:-}" ;;
  *) echo "usage: backup.sh once|daily|health|export [FILE]" >&2; exit 2 ;;
esac
