# Two images from one file:
#   --target prod  production: runtime dependencies only (hash-pinned lock), non-root, health check
#   (default)      development and CI: everything, including the tests and test tools
# The development image is the last stage, so `docker build ./backend` and compose.yaml are unchanged.
# Base images are pinned to multi-architecture index digests (amd64 and arm64); Dependabot proposes
# updates weekly, and docs/OPERATIONS.md (Updating base images) describes the manual procedure.

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS prod
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY requirements-runtime.lock .
# pip is not needed at run time; removing it (and its bundled wheel) also removes the copies of
# urllib3, msgpack and setuptools vendored inside it, which vulnerability scanners report.
RUN pip install --require-hashes --no-deps -r requirements-runtime.lock \
 && python -m pip uninstall --yes pip \
 && rm -rf /usr/local/lib/python3.13/ensurepip/_bundled
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
USER app
EXPOSE 8000
# Liveness only (the process answers); readiness, including the migration check, is /health/ready.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
# Forwarded client addresses are trusted only from FORWARDED_ALLOW_IPS (set to the reverse proxy).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS dev
WORKDIR /app
COPY requirements.txt requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
COPY eval ./eval
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
