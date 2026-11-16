# Production web image: the React app built once (on the build machine's own architecture, so
# multi-architecture builds stay fast) and served by Caddy as a non-root user.
# Build from the repository root:  docker build -f deploy/web.Dockerfile .

FROM --platform=$BUILDPLATFORM node:24-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM caddy:2.11-alpine
RUN addgroup -S -g 10002 web \
 && adduser -S -D -H -u 10002 -G web web \
 && chown -R web:web /data /config
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
# The official binary carries the capability to bind ports 80 and 443 without root.
USER web
