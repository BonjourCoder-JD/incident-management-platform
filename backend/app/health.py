"""Liveness, database and readiness checks (Milestone 6).

Readiness means the database answers AND its schema is exactly the revision this code was written
for. Running against an older schema is how the local dashboard failed with HTTP 500 at revision
0005 while the code expected 0008; with this check the API reports 503 and the SLA worker skips its
runs until migrations are applied.
"""
import logging
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from .database import engine

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent.parent

router = APIRouter(tags=["health"])
NO_STORE = {"Cache-Control": "no-store"}


@lru_cache(maxsize=1)
def _scripts() -> ScriptDirectory:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return ScriptDirectory.from_config(config)


def expected_revisions() -> frozenset[str]:
    """The migration head(s) shipped with this code."""
    return frozenset(_scripts().get_heads())


def database_revisions(conn: Connection) -> frozenset[str]:
    """The revision(s) recorded in the database; empty when it was never migrated."""
    if conn.execute(text("SELECT to_regclass('alembic_version')")).scalar_one() is None:
        return frozenset()
    return frozenset(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())


def readiness(bind: Engine | Connection | None = None, check_role: bool | None = None) -> tuple[bool, str]:
    """(ready, reason). Reasons: ok, database_unavailable, migrations_pending, unknown_database_revision,
    and in production unsafe_database_role (superuser, table owner, or able to modify audit tables)."""
    from .config import APP_ENV
    from .db_roles import role_problems

    bind = bind if bind is not None else engine
    check_role = APP_ENV == "production" if check_role is None else check_role
    try:
        with bind.connect() if isinstance(bind, Engine) else nullcontext(bind) as conn:
            current = database_revisions(conn)
            unsafe = role_problems(conn) if check_role else []
    except SQLAlchemyError:
        logger.warning("Not ready: database unavailable")
        return False, "database_unavailable"
    if unsafe:
        logger.warning("Not ready (unsafe_database_role): %s", ", ".join(unsafe))
        return False, "unsafe_database_role"
    expected = expected_revisions()
    if current == expected:
        return True, "ok"
    known = all(_known(revision) for revision in current)
    reason = "migrations_pending" if known else "unknown_database_revision"
    # Revision identifiers go to the log only, not to the public response.
    logger.warning("Not ready (%s): database at %s, code expects %s", reason, sorted(current) or "no revision", sorted(expected))
    return False, reason


def _known(revision: str) -> bool:
    try:
        return _scripts().get_revision(revision) is not None
    except Exception:  # alembic raises for identifiers it does not know
        return False


def get_health_bind() -> Engine | Connection:
    """Dependency; tests replace it with their rolled-back connection."""
    return engine


HealthBind = Annotated[object, Depends(get_health_bind)]


@router.get("/health")
def health():
    """Liveness: the process is up. Never touches the database."""
    return {"status": "ok"}


@router.get("/health/db")
def database_health(bind: HealthBind):
    try:
        with bind.connect() if isinstance(bind, Engine) else nullcontext(bind) as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"}, headers=NO_STORE)
    return JSONResponse(content={"status": "ok"}, headers=NO_STORE)


@router.get("/health/ready")
def ready(bind: HealthBind):
    """Readiness for load balancers, deploy checks and monitors: 503 until the schema matches the code."""
    ok, reason = readiness(bind)
    if not ok:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": reason}, headers=NO_STORE)
    return JSONResponse(content={"status": "ready"}, headers=NO_STORE)
