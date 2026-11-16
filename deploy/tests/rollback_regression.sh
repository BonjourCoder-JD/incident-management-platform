#!/usr/bin/env bash
# Regression test (Milestone 6 Phase E): a failed deployment that applied no migration rolls back BOTH the
# images and the server's git checkout, and the previous version passes the smoke test again.
#
#   deploy/tests/rollback_regression.sh <backend image> <web image> <backup image>
#
# It builds a deliberately broken variant of the backend image (sign-in raises), makes a throwaway git
# repository with two real commits ("good" and "broken") from the current tree, deploys good, then broken,
# with the real deploy/deploy.sh, and checks the exit status, the running image, the checkout and the
# recorded state. Everything runs in the Compose project imp-rollbacktest, which is removed at the end.
set -euo pipefail

BACKEND=${1:?usage: rollback_regression.sh <backend image> <web image> <backup image>}
WEB=${2:?}
BACKUP=${3:?}
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
readonly PROJECT=imp-rollbacktest
REPO_PREFIX=local/rollbacktest
WORK="$(mktemp -d)"
TREE="$WORK/repo"
mkdir -p "$TREE"

fail() { echo "ROLLBACK REGRESSION FAILED: $*" >&2; exit 1; }
cleanup() {
  if [[ -f "$TREE/deploy/.deploy/images.env" ]]; then
    (cd "$TREE" && docker compose -p "$PROJECT" -f deploy/compose.prod.yaml --env-file deploy/.env.production \
      --env-file deploy/.deploy/images.env --profile demo down --volumes --remove-orphans >/dev/null 2>&1) || true
  fi
  rm -rf -- "$WORK"
}
trap cleanup EXIT

echo "== A throwaway repository with two commits"
(cd "$SRC" && git ls-files -co --exclude-standard -z | tar --null -T - -cf -) | tar -C "$TREE" -xf -
cd "$TREE"
git init --quiet
git add --all
git -c user.name=rollback-test -c user.email=rollback-test@example.invalid commit --quiet -m good
GOOD="$(git rev-parse HEAD)"
git -c user.name=rollback-test -c user.email=rollback-test@example.invalid commit --quiet --allow-empty -m broken
BROKEN="$(git rev-parse HEAD)"

echo "== Images: good, and broken (sign-in raises; no migration)"
for version in "$GOOD" "$BROKEN"; do
  docker tag "$WEB" "$REPO_PREFIX/web:sha-$version"
  docker tag "$BACKUP" "$REPO_PREFIX/backup:sha-$version"
done
docker tag "$BACKEND" "$REPO_PREFIX/backend:sha-$GOOD"
docker build --quiet --tag "$REPO_PREFIX/backend:sha-$BROKEN" - >/dev/null <<EOF
FROM $BACKEND
USER root
RUN python -c "import pathlib; p = pathlib.Path('app/auth.py'); s = p.read_text(); \
old = 'def login(payload: LoginRequest, request: Request, response: Response, db: DbSession):\n'; \
assert s.count(old) == 1; p.write_text(s.replace(old, old + '    raise RuntimeError(\"broken on purpose\")\n'))"
USER app
EOF

umask 077
{
  echo "POSTGRES_USER=incident"
  echo "POSTGRES_DB=incidents_demo"
  echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
  echo "APP_DB_PASSWORD=$(openssl rand -hex 24)"
  echo "SITE_ADDRESS=localhost"
  echo "HTTP_PORT=${ROLLBACK_TEST_HTTP_PORT:-18080}"
  echo "HTTPS_PORT=${ROLLBACK_TEST_HTTPS_PORT:-18443}"
  echo "DEMO_MODE=true"
  echo "TRIAGE_PROVIDER=fake"
} >deploy/.env.production
export DEPLOY_PROJECT="$PROJECT" IMAGE_REPO="$REPO_PREFIX" DEPLOY_PULL=0 SMOKE_INSECURE=1
unset DEPLOY_ENV_FILE DEPLOY_STATE_DIR DEPLOY_SKIP_GIT_CHECK

echo "== Deploy good"
git checkout --quiet --detach "$GOOD"
bash deploy/deploy.sh "$GOOD" >"$WORK/good.log" 2>&1 || { cat "$WORK/good.log"; fail "the good version did not deploy"; }

echo "== Deploy broken: expect an automatic rollback of images and checkout"
git checkout --quiet --detach "$BROKEN"
status=0
bash deploy/deploy.sh "$BROKEN" >"$WORK/broken.log" 2>&1 || status=$?
grep -E '^\[deploy|FAIL|Smoke test' "$WORK/broken.log"
[[ "$status" == 1 ]] || fail "deploy.sh exited $status, expected 1"
grep -q "rolled back automatically" "$WORK/broken.log" || fail "no automatic rollback reported"
[[ "$(git rev-parse HEAD)" == "$GOOD" ]] || fail "the checkout is at $(git rev-parse HEAD), not the good version $GOOD"
running="$(docker inspect --format '{{.Config.Image}}' "$PROJECT-backend-1")"
[[ "$running" == "$REPO_PREFIX/backend:sha-$GOOD" ]] || fail "the running backend is $running"
status_out="$(bash deploy/deploy.sh --status)"
grep -qx "deployed: $GOOD at .*" <<<"$status_out" || fail "the recorded deployment is not the good version: $status_out"
SMOKE_RESOLVE=127.0.0.1 bash deploy/smoke.sh "https://localhost:${ROLLBACK_TEST_HTTPS_PORT:-18443}" >/dev/null ||
  fail "the good version does not pass the smoke test after the rollback"
echo "ROLLBACK REGRESSION PASSED: images, checkout and state restored to $GOOD"
