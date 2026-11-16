#!/usr/bin/env bash
# Deploy one published version to this server (Milestone 6 Phase E). Run from the repository root,
# with the checkout at that version (the Deploy workflow does this):
#
#   git fetch origin && git checkout --detach <commit> && bash deploy/deploy.sh <commit>
#   bash deploy/deploy.sh --status          the deployed version, the previous one and the database revision
#
# A rollback is a deployment of an earlier version (the previous one is shown by --status). Steps:
#  1. pull the version's images from GHCR and pin them by digest (deploy/.deploy/images.env);
#  2. refuse if the database is at a migration revision the target version does not know: rolling back
#     an image never undoes a migration (restore the pre-deploy backup instead; docs/OPERATIONS.md);
#  3. back up the database (backup.sh once), so a failed migration can be undone by a restore;
#  4. `up --wait` (the migrate service applies new migrations), then deploy/smoke.sh;
#  5. on failure: if no migration was applied, switch back to the previous version automatically (its
#     images and its checkout) and smoke-test it; if a migration was applied, stop and print the recovery
#     steps (never automatic).
# Exit status: 0 deployed; 1 failed (rolled back, or recovery needed: see the output); 2 refused.
set -euo pipefail
# An automatic rollback checks out the previous commit, which replaces this file; bash reads scripts
# while running them, so run from a private copy of the version that started the deployment.
if [[ -z "${DEPLOY_RUNNING_COPY:-}" ]]; then
  DEPLOY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  copy="$(mktemp)"
  cp "$0" "$copy"
  DEPLOY_ROOT="$DEPLOY_ROOT" DEPLOY_RUNNING_COPY="$copy" exec bash "$copy" "$@"
fi
cd "$DEPLOY_ROOT"
LOCKED=0
cleanup() {
  [[ "$LOCKED" == 1 ]] && rmdir "$STATE/lock" 2>/dev/null
  rm -f -- "$DEPLOY_RUNNING_COPY"
}
trap cleanup EXIT

ENV_FILE="${DEPLOY_ENV_FILE:-deploy/.env.production}"
STATE="${DEPLOY_STATE_DIR:-deploy/.deploy}"
IMAGES="$STATE/images.env"
PROJECT="${DEPLOY_PROJECT:-imp-prod}"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/bonjourcoder-jd/incident-management-platform}"
PULL="${DEPLOY_PULL:-1}"  # 0 only for tests with locally built images

log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $1"; exit "${2:-1}"; }

[[ -f "$ENV_FILE" ]] || die "missing $ENV_FILE (copy deploy/.env.production.example)" 2
mkdir -p "$STATE"
chmod 700 "$STATE"
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1; }
SITE_ADDRESS="$(env_value SITE_ADDRESS)"
HTTPS_PORT="$(env_value HTTPS_PORT)"
SITE_URL="${DEPLOY_SITE_URL:-https://$SITE_ADDRESS${HTTPS_PORT:+:$HTTPS_PORT}}"
PROFILE=()
[[ "$(env_value DEMO_MODE)" == true ]] && PROFILE=(--profile demo)
compose() {  # IMAGES_FILE compose-arguments...
  local images=$1
  shift
  # DEPLOY_EXTRA_COMPOSE: tests only (for example slower health checks under CPU emulation).
  docker compose -p "$PROJECT" -f deploy/compose.prod.yaml ${DEPLOY_EXTRA_COMPOSE:+-f "$DEPLOY_EXTRA_COMPOSE"} \
    --env-file "$ENV_FILE" --env-file "$images" "${PROFILE[@]}" "$@"
}
db_revision() {  # the database's migration revision, or "none" (fresh install or database not running)
  local images=$1
  [[ -f "$images" ]] || { echo none; return; }
  compose "$images" exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "SELECT version_num FROM alembic_version" 2>/dev/null' 2>/dev/null |
    tr -d '[:space:]' | grep -E '^[0-9a-z_]+$' || echo none
}
field() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1; }

if [[ "${1:-}" == "--status" ]]; then
  echo "deployed: $(field "$STATE/current" version) at $(field "$STATE/current" deployed_at)"
  echo "previous: $(field "$STATE/previous" version)"
  echo "database revision: $(db_revision "$IMAGES")"
  exit 0
fi

VERSION="${1:-}"
[[ "$VERSION" =~ ^[0-9a-f]{40}$ ]] || die "usage: deploy.sh <40-character commit SHA> | --status" 2
if [[ "${DEPLOY_SKIP_GIT_CHECK:-}" != 1 && "$(git rev-parse HEAD)" != "$VERSION" ]]; then
  die "the checkout is at $(git rev-parse --short HEAD), not $VERSION: run git checkout --detach $VERSION first" 2
fi
mkdir "$STATE/lock" 2>/dev/null ||
  die "another deployment is running (if none is, a crashed one left $STATE/lock: remove that directory)" 2
LOCKED=1
# The smoke test reaches Caddy on this machine, with the real hostname and certificate.
export SMOKE_RESOLVE="${SMOKE_RESOLVE-127.0.0.1}"

# 1. Images for this version, pinned by digest.
NEW="$STATE/images.next.env"
: >"$NEW"
for image in backend web backup; do
  ref="$IMAGE_REPO/$image:sha-$VERSION"
  if [[ "$PULL" == 1 ]]; then
    docker pull --quiet "$ref" >/dev/null || die "cannot pull $ref (is the version published?)"
    pinned="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$ref" | grep -m 1 "^$IMAGE_REPO/$image@sha256:")" ||
      die "no registry digest for $ref"
  else
    docker image inspect "$ref" >/dev/null 2>&1 || die "local image $ref not found"
    pinned="$ref"  # tests only: locally built images have no registry digest
  fi
  printf '%s_IMAGE=%s\n' "$(tr '[:lower:]' '[:upper:]' <<<"$image")" "$pinned" >>"$NEW"
done
BACKEND="$(field "$NEW" BACKEND_IMAGE)"
cp "$NEW" "$STATE/images-$VERSION.env"  # kept per version: rollbacks and restore checks use the right images
log "version $VERSION: $(tr '\n' ' ' <"$NEW")"

# 2. Migration compatibility: the target code must know the database's revision.
BEFORE="$(db_revision "$IMAGES")"
if [[ "$BEFORE" != none ]] && ! docker run --rm --entrypoint alembic "$BACKEND" show "$BEFORE" >/dev/null 2>&1; then
  die "the database is at migration $BEFORE, which version $VERSION does not know (it is older than the database).
Rolling back an image does not undo migrations. Either deploy a version that includes $BEFORE, or restore the
pre-deploy backup of the version you want into a new database and switch it in first (docs/OPERATIONS.md, Rollback).
Nothing was changed." 2
fi

# 3. Pre-deploy backup (not on a fresh install).
BACKUP_FILE=""
if [[ "$BEFORE" != none ]]; then
  out="$(compose "$IMAGES" run --rm --no-deps -T backup backup.sh once)" || die "the pre-deploy backup failed; nothing was changed"
  BACKUP_FILE="$(grep -oE '[a-z0-9_]+-[0-9]{8}T[0-9]{6}Z\.dump' <<<"$out" | head -n 1)"
  log "pre-deploy backup: $BACKUP_FILE (database at migration $BEFORE)"
fi

# 4. Switch to the new images and check them.
[[ -f "$IMAGES" ]] && cp "$IMAGES" "$STATE/images.previous.env"
cp "$NEW" "$IMAGES"
log "starting version $VERSION"
STARTED=0
compose "$IMAGES" up -d --remove-orphans --wait --wait-timeout "${DEPLOY_WAIT_SECONDS:-300}" && STARTED=1
AFTER="$(db_revision "$IMAGES")"
if [[ "$STARTED" == 1 ]] && bash deploy/smoke.sh "$SITE_URL"; then
  [[ -f "$STATE/current" ]] && cp "$STATE/current" "$STATE/previous"
  printf 'version=%s\ndeployed_at=%s\ndatabase_revision=%s\npre_deploy_backup=%s\n' "$VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$AFTER" "$BACKUP_FILE" >"$STATE/current"
  cat "$IMAGES" >>"$STATE/current"
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$VERSION" "$AFTER" >>"$STATE/history"
  log "deployed $VERSION (database at migration $AFTER)"
  exit 0
fi

# 5. Failure handling.
log "version $VERSION failed its start-up or smoke test (database now at migration $AFTER, before $BEFORE)"
if [[ ! -f "$STATE/images.previous.env" ]]; then
  die "this was the first deployment: nothing to roll back to. See the output above and: docker compose -p $PROJECT ps"
fi
if [[ "$AFTER" != "$BEFORE" ]]; then
  die "a migration ($BEFORE -> $AFTER) was applied, so the previous version cannot simply be restarted.
The failed version is left running. Recover with ONE of:
  a) deploy a fixed version (roll forward), or
  b) restore the pre-deploy backup $BACKUP_FILE into a new database, switch it in, then deploy the previous
     version $(field "$STATE/current" version) (docs/OPERATIONS.md, Rollback after a migration).
Changes made since the backup are lost in b)."
fi
PREVIOUS="$(field "$STATE/current" version)"
log "no migration was applied: switching back to version $PREVIOUS (images and checkout)"
# The previous version's files too (compose file, scripts), so they match the images that run again.
if [[ "${DEPLOY_SKIP_GIT_CHECK:-}" != 1 ]]; then
  git checkout --quiet --detach "$PREVIOUS" ||
    log "WARNING: could not check out $PREVIOUS; the images are rolled back, run git checkout --detach $PREVIOUS"
fi
cp "$STATE/images.previous.env" "$IMAGES"
if compose "$IMAGES" up -d --remove-orphans --wait --wait-timeout "${DEPLOY_WAIT_SECONDS:-300}" && bash deploy/smoke.sh "$SITE_URL"; then
  die "version $VERSION was rolled back automatically: $PREVIOUS is running again (checkout at $(git rev-parse --short HEAD)) and passed the smoke test."
fi
die "the automatic rollback also failed; see docker compose -p $PROJECT ps and the logs"
