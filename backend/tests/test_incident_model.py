from sqlalchemy import inspect, select
from app.database import Base, engine, SessionLocal
from app.models import Incident, IncidentStatus, Severity

def test_incident_table_exists():
    assert "incidents" in inspect(engine).get_table_names()

def test_incident_round_trip():
    # Roll back after test; no permanent demonstration records.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with SessionLocal(bind=connection) as session:
                incident = Incident(title="Suspicious sign-in", description="Synthetic test event", severity=Severity.HIGH)
                session.add(incident)
                session.flush()
                incident_id = incident.id
                session.expire_all()
                saved = session.scalar(select(Incident).where(Incident.id == incident_id))
                assert saved is not None
                assert saved.severity == Severity.HIGH
                assert saved.status == IncidentStatus.OPEN
                assert saved.created_at is not None
        finally:
            transaction.rollback()
