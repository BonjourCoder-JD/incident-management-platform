# Production web image: the React app built once (on the build machine's own architecture, so
# multi-architecture builds stay fast) and served by Caddy as a non-root user.
# Build from the repository root:  docker build -f deploy/web.Dockerfile .
# Base images are pinned to multi-architecture index digests (amd64 and arm64); Dependabot proposes
# updates weekly, and docs/OPERATIONS.md (Updating base images) describes the manual procedure.

FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM caddy:2.11-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0
RUN addgroup -S -g 10002 web \
 && adduser -S -D -H -u 10002 -G web web \
 && chown -R web:web /data /config
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
# The official binary carries the capability to bind ports 80 and 443 without root.
USER web
