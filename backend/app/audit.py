"""Incident audit trail: snapshots, diffs and event records.

Only the fields in AUDITED_FIELDS can ever appear in an audit record, so passwords, password
hashes, session and CSRF tokens, request headers and IP addresses cannot leak into it.
"""
import enum
from typing import Any

from sqlalchemy.orm import Session

from .models import Incident, IncidentEvent, IncidentEventAction, User

AUDITED_FIELDS = ("title", "description", "severity", "status", "assignee_id")
STATUS_FIELDS = {"status"}
ASSIGNMENT_FIELDS = {"assignee_id"}

Changes = dict[str, dict[str, Any]]


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, enum.Enum) else value


def snapshot(incident: Incident) -> dict[str, Any]:
    """JSON-safe values of the audited fields, read from the (locked) persisted row."""
    return {field: _plain(getattr(incident, field)) for field in AUDITED_FIELDS}


def diff(before: dict[str, Any], after: dict[str, Any]) -> Changes:
    return {
        field: {"from": before[field], "to": after[field]}
        for field in AUDITED_FIELDS
        if before[field] != after[field]
    }


def creation_changes(incident: Incident) -> Changes:
    return {field: {"from": None, "to": value} for field, value in snapshot(incident).items() if value is not None}


def classify(changes: Changes) -> IncidentEventAction:
    """One event per request: a specific action when only one kind of field changed, else `updated`."""
    fields = set(changes)
    if fields <= STATUS_FIELDS:
        return IncidentEventAction.STATUS_CHANGED
    if fields <= ASSIGNMENT_FIELDS:
        return IncidentEventAction.ASSIGNMENT_CHANGED
    return IncidentEventAction.UPDATED


def record_incident_event(db: Session, incident: Incident, actor: User, action: IncidentEventAction, changes: Changes) -> IncidentEvent:
    """Adds the event to the caller's session; it commits (or rolls back) with the incident change."""
    unexpected = set(changes) - set(AUDITED_FIELDS)
    if unexpected:
        raise ValueError(f"Refusing to audit non-allowlisted fields: {sorted(unexpected)}")
    event = IncidentEvent(incident_id=incident.id, actor_id=actor.id, action=action, changes=changes)
    db.add(event)
    return event
