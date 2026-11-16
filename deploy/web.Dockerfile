# Production web image: the React app built once (on the build machine's own architecture, so
# multi-architecture builds stay fast) and served by Caddy as a non-root user.
# Build from the repository root:  docker build -f deploy/web.Dockerfile .
# Base images are pinned to multi-architecture index digests (amd64 and arm64); Dependabot proposes
# updates weekly, and docs/OPERATIONS.md (Updating base images) describes the manual procedure.

FROM --platform=$BUILDPLATFORM node:24-alpine@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5 AS build
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
