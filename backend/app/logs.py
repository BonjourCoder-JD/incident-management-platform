"""Structured logs and request IDs (Milestone 6 Phase D).

LOG_FORMAT=json (the production compose file) writes one JSON object per line to stdout:
time, level, logger, message, request_id (inside a request), selected fields, and the exception type
and traceback. LOG_FORMAT=text (the default, for development) keeps the readable format.

What never reaches a log line:
- request bodies, query strings, headers and cookies (so no passwords, session tokens, CSRF tokens
  or incident text): the request log records the method, the route template, status and duration;
- SQL parameters (the engine is created with hide_parameters=True);
- PostgreSQL DETAIL lines and "Failing row contains" text, which can echo row values;
- anything that looks like a password in a connection string, a key=value secret, a bearer token or
  an Anthropic API key (scrubbed as a last line of defence).
Application messages already contain identifiers only (tests check this per module).
"""
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
REQUEST_ID_HEADER = b"x-request-id"
request_logger = logging.getLogger("app.request")

_SCRUBBERS = (
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),  # postgresql://user:password@host
    # key=value, key: value and "key": "value", including names such as APP_DB_PASSWORD or csrf_token.
    (re.compile(r"(?i)(\b\w*(?:password|passwd|pwd|secret|token|api[_-]?key)[\"']?)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"), r"\1\2***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]+"), "sk-ant-***"),
    (re.compile(r"(?m)^(\s*DETAIL:).*$"), r"\1 [withheld]"),
    (re.compile(r"Failing row contains \(.*?\)\.?", re.S), "Failing row contains [withheld]."),
    (re.compile(r"\[parameters: .*?\]", re.S), "[parameters withheld]"),
)
# LogRecord attributes that are not "extra" fields.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}


def scrub(text: str) -> str:
    for pattern, replacement in _SCRUBBERS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _STANDARD and key != "request_id" and not key.startswith("_"):
                entry[key] = value if isinstance(value, (int, float, bool)) or value is None else scrub(str(value))
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
            entry["exc"] = scrub("".join(traceback.format_exception(*record.exc_info)))
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))  # compact, like deploy/backup.sh


def log_format() -> str:
    return os.environ.get("LOG_FORMAT", "text").strip().lower()


def configure(level: int = logging.INFO) -> None:
    """Idempotent. JSON: one stdout handler on the root logger; uvicorn's loggers propagate to it and
    its access log (client address and full query string) is replaced by the request log below."""
    logging.getLogger("alembic").setLevel(logging.WARNING)  # readiness loads migration scripts; their setup lines are noise
    if log_format() != "json":
        logging.basicConfig(level=level, format=TEXT_FORMAT)
        return
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore"):  # HTTP client request lines (full URLs) are not needed
        logging.getLogger(name).setLevel(logging.WARNING)


class RequestContextMiddleware:
    """Pure ASGI middleware: a fresh request ID per request (client-supplied IDs are ignored), the
    X-Request-ID response header, one request log line, and a JSON 500 that names the request ID
    instead of an unlogged crash."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        state = {"status": 500, "sent": False}

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                state["status"], state["sent"] = message["status"], True
                message = {**message, "headers": [*message.get("headers", []), (REQUEST_ID_HEADER, request_id.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            request_logger.exception("Unhandled error")
            if state["sent"]:
                raise
            from fastapi.responses import JSONResponse
            response = JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": request_id})
            await response(scope, receive, send_with_id)
        finally:
            route = scope.get("route")
            request_logger.info("request", extra={
                "method": scope.get("method"),
                "path": getattr(route, "path", None) or "<unmatched>",  # the template, never the raw path or query
                "status": state["status"],
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            })
            request_id_var.reset(token)
