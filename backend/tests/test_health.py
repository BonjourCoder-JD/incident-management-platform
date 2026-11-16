"""Liveness, database health, migration readiness and production settings (Milestone 6)."""
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import cli, health
from app.config import production_problems
from app.health import get_health_bind
from app.main import app

client = TestClient(app)


@pytest.fixture
def use_bind(connection):
    """Health checks read the test's rolled-back connection, so schema changes below are undone."""
    app.dependency_overrides[get_health_bind] = lambda: connection
    return connection


def test_health():
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_database_health(use_bind):
    response = client.get('/health/db')
    assert (response.status_code, response.json()) == (200, {'status': 'ok'})
    assert response.headers['cache-control'] == 'no-store'


def test_ready_when_the_schema_matches_the_code(use_bind):
    assert health.expected_revisions() == frozenset({'0008'})
    response = client.get('/health/ready')
    assert (response.status_code, response.json()) == (200, {'status': 'ready'})
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('statement, reason', [
    # An older schema: the 0005-versus-0008 dashboard outage.
    ("UPDATE alembic_version SET version_num = '0005'", 'migrations_pending'),
    ("UPDATE alembic_version SET version_num = '0007'", 'migrations_pending'),
    # Never migrated at all.
    ('ALTER TABLE alembic_version RENAME TO alembic_version_hidden', 'migrations_pending'),
    # A newer schema from a later release, for example after rolling back only the application.
    ("UPDATE alembic_version SET version_num = 'ffffffff'", 'unknown_database_revision'),
])
def test_not_ready_returns_503_until_the_schema_matches(statement, reason, use_bind, caplog):
    use_bind.execute(text(statement))
    response = client.get('/health/ready')
    assert response.status_code == 503
    assert response.json() == {'status': 'not_ready', 'reason': reason}
    assert '0008' not in response.text and '0005' not in response.text  # revisions are logged, not published
    assert 'code expects' in caplog.text


def test_unreachable_database_is_not_ready():
    unreachable = create_engine('postgresql+psycopg://nobody:none@127.0.0.1:1/none', connect_args={'connect_timeout': 1})
    app.dependency_overrides[get_health_bind] = lambda: unreachable
    assert client.get('/health/ready').json() == {'status': 'not_ready', 'reason': 'database_unavailable'}
    response = client.get('/health/db')
    assert (response.status_code, response.json()) == (503, {'detail': 'Database unavailable'})


def test_the_worker_skips_runs_until_the_database_is_ready(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(health, 'readiness', lambda bind=None: (False, 'migrations_pending'))
    monkeypatch.setattr('app.escalation.run_escalations', lambda *args, **kwargs: calls.append(1))
    assert cli.sla_escalate(loop=False, interval=60) == 1
    assert calls == []
    assert 'skipped: database not ready (migrations_pending)' in capsys.readouterr().err


# ---- Production settings --------------------------------------------------------------------

SAFE = {
    'DATABASE_URL': 'postgresql+psycopg://app:generated-secret-0123456789abcdef@db:5432/incidents',
    'SESSION_COOKIE_SECURE': 'true',
    'TRIAGE_PROVIDER': 'fake',
    'FORWARDED_ALLOW_IPS': '172.30.0.10',
}


def test_safe_production_settings_have_no_problems():
    assert production_problems(SAFE) == []


@pytest.mark.parametrize('change, problem', [
    ({'SESSION_COOKIE_SECURE': 'false'}, 'SESSION_COOKIE_SECURE must be true'),
    ({'DATABASE_URL': ''}, 'DATABASE_URL must be set'),
    ({'DATABASE_URL': 'postgresql+psycopg://incident:localdevonly@db:5432/incidents'}, 'development password'),
    ({'DATABASE_URL': 'postgresql+psycopg://app@db:5432/incidents'}, 'at least 24 characters'),
    ({'DATABASE_URL': 'postgresql+psycopg://app:short-secret@db:5432/incidents'}, 'at least 24 characters'),
    ({'TRIAGE_PROVIDER': 'anthropic'}, 'TRIAGE_PROVIDER=anthropic is not allowed'),
    ({'ANTHROPIC_API_KEY': 'sk-ant-synthetic-canary'}, 'ANTHROPIC_API_KEY must not be set'),
    ({'FORWARDED_ALLOW_IPS': '*'}, 'FORWARDED_ALLOW_IPS must name the reverse proxy'),
    ({'FORWARDED_ALLOW_IPS': ''}, 'FORWARDED_ALLOW_IPS must name the reverse proxy'),
])
def test_unsafe_production_settings_are_reported_without_secrets(change, problem):
    problems = production_problems({**SAFE, **change})
    assert any(problem in p for p in problems), problems
    assert not any('sk-ant' in p or 'localdevonly' in p or 'generated-secret' in p or 'short-secret' in p for p in problems)


def _import_app(env: dict) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k not in SAFE and k not in ('ANTHROPIC_API_KEY', 'APP_ENV')}
    code = 'import app.main as m; print("docs", m.app.docs_url, m.app.openapi_url)'
    return subprocess.run([sys.executable, '-c', code], env={**base, **env}, capture_output=True, text=True, timeout=60)


def test_production_refuses_to_start_with_unsafe_settings():
    result = _import_app({**SAFE, 'APP_ENV': 'production', 'TRIAGE_PROVIDER': 'anthropic', 'ANTHROPIC_API_KEY': 'sk-ant-synthetic-canary'})
    assert result.returncode != 0
    assert 'Unsafe production configuration' in result.stderr
    assert 'sk-ant-synthetic-canary' not in result.stderr


def test_production_starts_with_safe_settings_and_hides_the_api_docs():
    result = _import_app({**SAFE, 'APP_ENV': 'production'})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'docs None None'
    development = _import_app({**SAFE, 'APP_ENV': 'development'})
    assert development.stdout.strip() == 'docs /docs /openapi.json'
