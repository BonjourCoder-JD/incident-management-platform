#!/usr/bin/env bash
# Deployment smoke test (Milestone 6 Phase E): readiness, the web app, authentication and the dashboard,
# through the public entry point (Caddy). It only reads; the one session it opens is closed again.
#
#   deploy/smoke.sh https://demo-name.duckdns.org
#
# Credentials: in the public demo the published demo responder (from GET /api/demo); otherwise a staff
# account in SMOKE_EMAIL and SMOKE_PASSWORD (environment only; never printed).
# SMOKE_RESOLVE=127.0.0.1 sends the requests to this machine while keeping the real hostname and
# certificate (used on the server itself). SMOKE_INSECURE=1 accepts a locally issued certificate
# (local and CI test stacks only). Exit status 0 only if every check passed.
set -euo pipefail

BASE="${1:?usage: smoke.sh https://host[:port]}"
[[ "$BASE" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || { echo "smoke: the address must be https://host[:port]" >&2; exit 2; }
HOSTPORT="${BASE#https://}"
HOST="${HOSTPORT%%:*}"
PORT="443"
[[ "$HOSTPORT" == *:* ]] && PORT="${HOSTPORT##*:}"

WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT
JAR="$WORK/cookies"
CURL=(curl --silent --show-error --max-time 15 --proto '=https' --cookie "$JAR" --cookie-jar "$JAR")
[[ -n "${SMOKE_RESOLVE:-}" ]] && CURL+=(--resolve "$HOST:$PORT:$SMOKE_RESOLVE")
[[ "${SMOKE_INSECURE:-}" == 1 ]] && CURL+=(--insecure)
FAILED=0

pass() { echo "  ok    $*"; }
fail() { echo "  FAIL  $*" >&2; FAILED=1; }
request() {  # METHOD PATH [JSON body] -> sets STATUS, BODY, HEADERS
  local method=$1 path=$2 body=${3:-}
  local -a extra=(--request "$method" --dump-header "$WORK/headers" --output "$WORK/body" --write-out '%{http_code}')
  [[ -n "$body" ]] && extra+=(--header "Content-Type: application/json" --data-binary @-)
  [[ -n "${CSRF:-}" ]] && extra+=(--header "X-CSRF-Token: $CSRF")
  STATUS="$(printf '%s' "$body" | "${CURL[@]}" "${extra[@]}" "$BASE$path" 2>"$WORK/error" || true)"
  BODY="$(cat "$WORK/body" 2>/dev/null || true)"
  HEADERS="$(tr -d '\r' <"$WORK/headers" 2>/dev/null || true)"
}
json_string() { sed -n "s/.*\"$1\":\"\\([^\"]*\\)\".*/\\1/p" <<<"$BODY" | head -n 1; }

echo "Smoke test of $BASE"

# 1. Readiness (the database answers and its schema matches this code); allow a minute after start-up.
for _ in $(seq 1 30); do
  request GET /api/health/ready
  [[ "$STATUS" == 200 ]] && break
  sleep 2
done
[[ "$STATUS" == 200 && "$BODY" == *'"ready"'* ]] && pass "readiness 200" || fail "readiness: HTTP $STATUS $(head -c 200 <<<"$BODY")"

# 2. The web app and its security headers.
request GET /
if [[ "$STATUS" == 200 && "$BODY" == *'id="root"'* ]]; then pass "web app 200"; else fail "web app: HTTP $STATUS"; fi
grep -qi '^content-security-policy: default-src' <<<"$HEADERS" && grep -qi '^strict-transport-security:' <<<"$HEADERS" &&
  pass "security headers present" || fail "security headers missing"
request GET /dashboard
[[ "$STATUS" == 200 && "$BODY" == *'id="root"'* ]] && pass "dashboard page 200" || fail "dashboard page: HTTP $STATUS"

# 3. Authentication: a wrong password is refused; a staff account signs in.
EMAIL="${SMOKE_EMAIL:-}"
PASSWORD="${SMOKE_PASSWORD:-}"
if [[ -z "$EMAIL" ]]; then
  request GET /api/demo
  if [[ "$BODY" == *'"enabled":true'* ]]; then
    EMAIL="$(grep -o '{[^}]*"role":"responder"[^}]*}' <<<"$BODY" | sed -n 's/.*"email":"\([^"]*\)".*/\1/p')"
    PASSWORD="$(grep -o '{[^}]*"role":"responder"[^}]*}' <<<"$BODY" | sed -n 's/.*"password":"\([^"]*\)".*/\1/p')"
  fi
fi
if [[ -z "$EMAIL" || -z "$PASSWORD" ]]; then
  fail "no account: not a public demo, and SMOKE_EMAIL and SMOKE_PASSWORD are not set"
else
  request POST /api/auth/login "{\"email\":\"smoke-$RANDOM$RANDOM@example.com\",\"password\":\"not-the-password-1\"}"
  [[ "$STATUS" == 401 ]] && pass "wrong credentials refused (401)" || fail "wrong credentials: HTTP $STATUS"
  request POST /api/auth/login "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"
  CSRF="$(json_string csrf_token)"
  [[ "$STATUS" == 200 && -n "$CSRF" ]] && pass "sign-in 200" || fail "sign-in: HTTP $STATUS"
  request GET /api/auth/me
  [[ "$STATUS" == 200 && "$BODY" == *'"role":"'* ]] && pass "session 200 (role $(json_string role))" || fail "session: HTTP $STATUS"

  # 4. Dashboard data and the incident list.
  request GET /api/analytics/summary
  [[ "$STATUS" == 200 && "$BODY" == *'"totals"'* && "$BODY" == *'"sla"'* ]] && pass "dashboard summary 200" || fail "dashboard summary: HTTP $STATUS"
  request GET /api/incidents
  [[ "$STATUS" == 200 && "$BODY" == *'"items"'* ]] && pass "incident list 200" || fail "incident list: HTTP $STATUS"

  # 5. Sign out; the session no longer works.
  request POST /api/auth/logout
  [[ "$STATUS" == 204 ]] && pass "sign-out 204" || fail "sign-out: HTTP $STATUS"
  CSRF=""
  request GET /api/auth/me
  [[ "$STATUS" == 401 ]] && pass "session ended (401)" || fail "after sign-out: HTTP $STATUS"
fi

if [[ "$FAILED" == 0 ]]; then echo "Smoke test passed."; else echo "Smoke test FAILED." >&2; fi
exit "$FAILED"
