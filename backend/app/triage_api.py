"""AI triage endpoints (Milestone 5C). Responders and admins only; reporters get 403.

- GET  /triage/status                        whether triage is available, the provider, limits
- POST /incidents/{id}/triage-suggestions    request a suggestion (CSRF; budgets and rate limits)
- GET  /incidents/{id}/triage-suggestions    suggestions for an incident, newest first
- POST /triage-suggestions/{id}/decision     accept or reject (CSRF); accepting applies the chosen
                                             severity through the normal audited update path
Requesting a suggestion never changes the incident.
"""
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import triage
from .auth import require_roles
from .config import TriageSettings, triage_settings
from .database import get_db
from .incidents import _get_visible
from .models import Incident, TriageDecision, TriageOutcome, TriageRequest, TriageSuggestion, User, UserRole
from .schemas import (
    TriageCandidate, TriageDecisionCreate, TriageDecisionRead, TriageLimits, TriageStatus, TriageSuggestionPage, TriageSuggestionRead,
    UserSummary,
)
from .triage_providers import PROMPT_VERSION, TriageProvider, build_provider

router = APIRouter(tags=["triage"])
DbSession = Annotated[Session, Depends(get_db)]
Staff = Annotated[User, Depends(require_roles(UserRole.RESPONDER, UserRole.ADMIN))]

SENT_FIELDS = ["title", "description", "severity", "status", "duplicate candidate titles (up to 5)"]
# A typical request: about 2,000 input and 600 output tokens.
TYPICAL_TOKENS = (2000, 600)
MESSAGES = {
    TriageOutcome.INVALID_OUTPUT: "The AI response was not usable, so it was discarded. Continue triage manually.",
    TriageOutcome.REFUSED: "The AI declined to suggest a triage for this incident. Continue triage manually.",
    TriageOutcome.TIMEOUT: "The AI service did not respond in time. Continue triage manually.",
    TriageOutcome.PROVIDER_ERROR: "The AI service is unavailable. Continue triage manually.",
    TriageOutcome.ABANDONED: "This request did not complete.",
}


def get_triage_settings() -> TriageSettings:
    return triage_settings


@lru_cache(maxsize=4)
def _cached_provider(settings: TriageSettings) -> tuple[TriageProvider | None, str]:
    return build_provider(settings)


def get_triage_provider(settings: Annotated[TriageSettings, Depends(get_triage_settings)]) -> tuple[TriageProvider | None, str]:
    """Dependency; tests replace it with the fake or a scripted provider."""
    return _cached_provider(settings)


Settings = Annotated[TriageSettings, Depends(get_triage_settings)]
Provider = Annotated[tuple, Depends(get_triage_provider)]


def _read(db: Session, suggestion: TriageSuggestion) -> TriageSuggestionRead:
    request = suggestion.request
    ids = list(request.candidate_ids)
    titles = dict(db.execute(select(Incident.id, Incident.title).where(Incident.id.in_(ids))).all()) if ids else {}
    decision = db.scalar(select(TriageDecision).where(TriageDecision.suggestion_id == suggestion.id))
    return TriageSuggestionRead(
        id=suggestion.id,
        incident_id=request.incident_id,
        outcome=suggestion.outcome,
        message=MESSAGES.get(suggestion.outcome),
        severity=suggestion.severity,
        rationale=suggestion.rationale,
        next_steps=suggestion.next_steps or [],
        duplicates=[TriageCandidate(id=i, title=titles[i]) for i in (suggestion.duplicate_ids or []) if i in titles],
        candidates_sent=[TriageCandidate(id=i, title=titles[i]) for i in ids if i in titles],
        provider=request.provider,
        model=request.model,
        prompt_version=request.prompt_version,
        requested_by=UserSummary.model_validate(request.requester),
        created_at=suggestion.created_at,
        cost_usd=suggestion.cost_microusd / 1_000_000,
        stale=triage.latest_event_id(db, request.incident_id) != request.basis_event_id,
        decision=TriageDecisionRead.model_validate(decision) if decision is not None else None,
    )


def _limits(db: Session, settings: TriageSettings, user: User) -> TriageLimits:
    day_start = db.scalar(select(func.date_trunc("day", func.now())))
    spent = triage._spent(db, TriageRequest.requested_by == user.id, TriageRequest.created_at >= day_start)
    recent = db.scalar(select(func.count()).select_from(TriageRequest).where(
        TriageRequest.requested_by == user.id, TriageRequest.created_at > func.now() - func.make_interval(0, 0, 0, 0, 1)))
    return TriageLimits(
        user_daily_usd=settings.user_daily_usd,
        user_daily_remaining_usd=max(0.0, settings.user_daily_usd - spent / 1_000_000),
        user_hourly_requests=settings.user_hourly_requests,
        user_hourly_remaining=max(0, settings.user_hourly_requests - recent),
        incident_cooldown_seconds=settings.incident_cooldown_seconds,
    )


@router.get("/triage/status", response_model=TriageStatus)
def triage_status(user: Staff, db: DbSession, settings: Settings, provider: Provider):
    instance, reason = provider
    prices = settings.prices()
    typical = triage.cost_microusd(*TYPICAL_TOKENS, prices) / 1_000_000 if instance is not None and prices else None
    return TriageStatus(
        enabled=instance is not None,
        reason=None if instance is not None else reason,
        provider=instance.name if instance is not None else ("off" if settings.provider not in ("fake", "anthropic") else settings.provider),
        provider_label=instance.label if instance is not None else None,
        is_ai=instance is not None and instance.name != "fake",
        model=instance.model if instance is not None else None,
        prompt_version=PROMPT_VERSION,
        sent_fields=SENT_FIELDS,
        typical_cost_usd=typical,
        limits=_limits(db, settings, user) if instance is not None else None,
    )


@router.post("/incidents/{incident_id}/triage-suggestions", response_model=TriageSuggestionRead, status_code=status.HTTP_201_CREATED)
def request_triage(incident_id: int, user: Staff, db: DbSession, settings: Settings, provider: Provider):
    incident = _get_visible(db, incident_id, user)  # no row lock: the provider call must not hold one
    instance, reason = provider
    if instance is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=reason)
    try:
        suggestion = triage.request_suggestion(db, settings, instance, user, incident)
    except triage.TriageLimitExceeded as limit:
        db.rollback()
        return JSONResponse(status_code=429, content={"detail": limit.message}, headers={"Retry-After": str(limit.retry_after)})
    return _read(db, suggestion)


@router.get("/incidents/{incident_id}/triage-suggestions", response_model=TriageSuggestionPage)
def list_triage(incident_id: int, user: Staff, db: DbSession):
    _get_visible(db, incident_id, user)
    suggestions = db.scalars(
        select(TriageSuggestion).join(TriageRequest, TriageRequest.id == TriageSuggestion.request_id)
        .where(TriageRequest.incident_id == incident_id).order_by(TriageSuggestion.id.desc()).limit(20)
    ).all()
    return TriageSuggestionPage(items=[_read(db, s) for s in suggestions])


@router.post("/triage-suggestions/{suggestion_id}/decision", response_model=TriageSuggestionRead)
def decide_triage(suggestion_id: int, payload: TriageDecisionCreate, user: Staff, db: DbSession):
    suggestion = db.get(TriageSuggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    # A short row lock for the decision only; no provider call happens here.
    incident = _get_visible(db, suggestion.request.incident_id, user, for_update=True)
    try:
        triage.decide(db, user, suggestion, incident, payload.decision, payload.severity, payload.add_internal_note, payload.note, payload.reason)
    except triage.TriageConflict as conflict:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict.message) from None
    except ValueError as invalid:  # an edited note that fails the comment rules
        db.rollback()
        raise HTTPException(status_code=422, detail=[{"loc": ["body", "note"], "msg": str(invalid), "type": "value_error"}]) from None
    db.commit()
    db.refresh(suggestion)
    return _read(db, suggestion)
