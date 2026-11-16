"""AI-assisted triage service (Milestone 5C): budgets, the provider call, validation and decisions.

Request flow (no incident row lock or database transaction is held while the provider runs):
1. Read the incident (no lock) and pick duplicate candidates the requester can see.
2. Short transaction under the triage budget advisory lock: close stale reservations as
   abandoned (at full cost), check every limit counting unresolved reservations at their full
   worst-case cost, insert the triage_requests row (the reservation), commit.
3. Call the provider with no transaction open.
4. Short transaction: validate the output strictly and insert triage_suggestions with the actual
   cost (or the full reservation when the provider may have billed without reporting it).
A crash between 2 and 4 leaves a reservation that keeps counting at full cost until it is closed
as abandoned, so budgets can only ever be over-counted, never under-counted.

Nothing here changes an incident. Only decide() does, after a person accepts a suggestion, through
the normal audited update path (incidents.apply_incident_changes) with that person as the actor.
"""
import hashlib
import logging
import math
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import TriageSettings
from .incidents import _visible_to
from .models import (
    CommentVisibility, Incident, IncidentEvent, Severity, TriageDecision, TriageDecisionKind, TriageOutcome, TriageRequest,
    TriageSuggestion, User,
)
from .schemas import _COMMENT_FORBIDDEN, clean_comment_body
from .triage_providers import PROMPT_VERSION, SYSTEM_PROMPT, ProviderResult, TriageInput, TriageProvider

logger = logging.getLogger(__name__)

TRIAGE_BUDGET_LOCK = 51_000_003
MAX_CANDIDATES = 5
RATIONALE_MAX = 600
STEP_MAX = 200
MAX_STEPS = 5
# Rough upper bound for the structured-output schema and request framing, in tokens.
REQUEST_OVERHEAD_TOKENS = 400
# No links, web addresses or email addresses in anything the model writes.
_LINK = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\bwww\.|[\w.+-]+@[\w-]+\.[\w.-]+|\b[a-z0-9-]+\.(?:com|net|org|io|co|ru|info|biz|xyz|top|app|dev|me|ly)\b)"
)


class TriageLimitExceeded(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.message, self.retry_after = message, max(1, retry_after)


class TriageValidationError(Exception):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class TriageConflict(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---- Output validation ------------------------------------------------------------------------------

def _clean_text(value: object, limit: int, category: str) -> str:
    if not isinstance(value, str):
        raise TriageValidationError(category)
    value = " ".join(value.split())  # collapse whitespace, including line breaks
    if not value or len(value) > limit or _COMMENT_FORBIDDEN.search(value) or _LINK.search(value):
        raise TriageValidationError(category)
    return value


@dataclass(frozen=True)
class ValidSuggestion:
    severity: Severity
    rationale: str
    next_steps: list[str]
    duplicate_ids: list[int]


def validate_output(raw: object, candidate_ids: list[int]) -> ValidSuggestion:
    """Strict validation of a model output. Anything unexpected is rejected, never repaired."""
    if not isinstance(raw, dict) or set(raw) != {"severity", "rationale", "next_steps", "duplicate_ids"}:
        raise TriageValidationError("schema")
    if raw["severity"] not in {s.value for s in Severity}:
        raise TriageValidationError("severity")
    rationale = _clean_text(raw["rationale"], RATIONALE_MAX, "rationale")
    steps = raw["next_steps"]
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise TriageValidationError("next_steps")
    steps = [_clean_text(step, STEP_MAX, "next_steps") for step in steps]
    duplicates = raw["duplicate_ids"]
    if (
        not isinstance(duplicates, list)
        or len(duplicates) > MAX_CANDIDATES
        or any(type(d) is not int for d in duplicates)
        or len(set(duplicates)) != len(duplicates)
        or not set(duplicates) <= set(candidate_ids)
    ):
        raise TriageValidationError("duplicates")
    return ValidSuggestion(Severity(raw["severity"]), rationale, steps, list(duplicates))


# ---- Input --------------------------------------------------------------------------------------------

# The stored generated column (title and description), indexed by ix_incidents_search.
_SEARCH_VECTOR = Incident.search_vector


def duplicate_candidates(db: Session, incident: Incident, user: User) -> list[tuple[int, str]]:
    """Up to five other incidents the requester can see whose text matches this title."""
    words = sorted({w for w in re.findall(r"[a-z0-9]{3,}", incident.title.lower())})[:12]
    if not words:
        return []
    query = func.to_tsquery("english", " | ".join(words))
    rows = db.execute(
        _visible_to(
            select(Incident.id, Incident.title)
            .where(Incident.id != incident.id, _SEARCH_VECTOR.op("@@")(query))
            .order_by(func.ts_rank(_SEARCH_VECTOR, query).desc(), Incident.id.desc())
            .limit(MAX_CANDIDATES),
            user,
        )
    ).all()
    return [(row.id, row.title) for row in rows]


def build_input(incident: Incident, candidates: list[tuple[int, str]]) -> TriageInput:
    return TriageInput(
        title=incident.title, description=incident.description, severity=incident.severity.value,
        status=incident.status.value, candidates=tuple(candidates),
    )


def input_digest(triage_input: TriageInput) -> str:
    return hashlib.sha256((SYSTEM_PROMPT + "\n" + triage_input.user_message()).encode("utf-8")).hexdigest()


def latest_event_id(db: Session, incident_id: int) -> int | None:
    return db.scalar(select(func.max(IncidentEvent.id)).where(IncidentEvent.incident_id == incident_id))


# ---- Costs and budgets ------------------------------------------------------------------------------------

def cost_microusd(input_tokens: int, output_tokens: int, prices: tuple[float, float]) -> int:
    """US$ per million tokens equals micro-US$ per token; always rounded up."""
    return math.ceil(input_tokens * prices[0] + output_tokens * prices[1])


def worst_case_microusd(triage_input: TriageInput, settings: TriageSettings, prices: tuple[float, float]) -> int:
    """An upper bound for one call: at most one token per UTF-8 byte of input, plus the full output allowance."""
    input_bound = len(SYSTEM_PROMPT.encode("utf-8")) + len(triage_input.user_message().encode("utf-8")) + REQUEST_OVERHEAD_TOKENS
    return cost_microusd(input_bound, settings.max_output_tokens, prices)


def _usd(value: float) -> int:
    return math.floor(value * 1_000_000)


def _spent(db: Session, *conditions) -> int:
    """Actual cost of resolved requests plus the full reservation of unresolved ones."""
    return db.scalar(
        select(func.coalesce(func.sum(func.coalesce(TriageSuggestion.cost_microusd, TriageRequest.reserved_microusd)), 0))
        .select_from(TriageRequest)
        .outerjoin(TriageSuggestion, TriageSuggestion.request_id == TriageRequest.id)
        .where(*conditions)
    )


def close_abandoned(db: Session, settings: TriageSettings) -> int:
    """Records reservations that never got a result as abandoned, charged in full."""
    cutoff = func.now() - timedelta(seconds=settings.reservation_timeout_seconds)
    stale = db.scalars(
        select(TriageRequest)
        .outerjoin(TriageSuggestion, TriageSuggestion.request_id == TriageRequest.id)
        .where(TriageSuggestion.id.is_(None), TriageRequest.created_at < cutoff)
    ).all()
    for request in stale:
        db.add(TriageSuggestion(request_id=request.id, outcome=TriageOutcome.ABANDONED, error_category="abandoned",
                                cost_microusd=request.reserved_microusd))
        logger.warning("Triage reservation %s closed as abandoned", request.id)
    db.flush()
    return len(stale)


def check_limits(db: Session, settings: TriageSettings, user: User, incident_id: int, reservation: int) -> None:
    """Raises TriageLimitExceeded if this reservation would break any limit. Caller holds the lock."""
    now = db.scalar(select(func.now()))
    day_start = db.scalar(select(func.date_trunc("day", func.now())))
    month_start = db.scalar(select(func.date_trunc("month", func.now())))
    to_tomorrow = int((day_start + timedelta(days=1) - now).total_seconds())
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    to_next_month = int((next_month - now).total_seconds())

    last = db.scalar(select(func.max(TriageRequest.created_at)).where(TriageRequest.incident_id == incident_id))
    if last is not None and now - last < timedelta(seconds=settings.incident_cooldown_seconds):
        wait = settings.incident_cooldown_seconds - int((now - last).total_seconds())
        raise TriageLimitExceeded("A suggestion was just requested for this incident. Try again shortly.", wait)
    hour_ago = now - timedelta(hours=1)
    recent = db.scalar(select(func.count()).select_from(TriageRequest).where(TriageRequest.requested_by == user.id, TriageRequest.created_at > hour_ago))
    if recent >= settings.user_hourly_requests:
        raise TriageLimitExceeded("You have reached your hourly limit for AI suggestions.", 3600)
    if _spent(db, TriageRequest.incident_id == incident_id) + reservation > _usd(settings.incident_total_usd):
        raise TriageLimitExceeded("The AI spending limit for this incident has been reached.", to_tomorrow)
    if _spent(db, TriageRequest.requested_by == user.id, TriageRequest.created_at >= day_start) + reservation > _usd(settings.user_daily_usd):
        raise TriageLimitExceeded("You have reached your daily AI spending limit.", to_tomorrow)
    today = db.scalar(select(func.count()).select_from(TriageRequest).where(TriageRequest.created_at >= day_start))
    if today >= settings.global_daily_requests:
        raise TriageLimitExceeded("AI triage is paused until tomorrow (daily request limit).", to_tomorrow)
    if _spent(db, TriageRequest.created_at >= day_start) + reservation > _usd(settings.global_daily_usd):
        raise TriageLimitExceeded("AI triage is paused until tomorrow (daily spending limit).", to_tomorrow)
    if _spent(db, TriageRequest.created_at >= month_start) + reservation > _usd(settings.global_monthly_usd):
        raise TriageLimitExceeded("AI triage is paused until next month (monthly spending limit).", to_next_month)


def reserve(db: Session, settings: TriageSettings, provider: TriageProvider, user: User, incident: Incident,
            triage_input: TriageInput, candidate_ids: list[int]) -> TriageRequest:
    """Step 2: one short transaction under the budget lock; commits the reservation."""
    prices = settings.prices()
    assert prices is not None, "prices are required (checked when the provider is built)"
    reservation = worst_case_microusd(triage_input, settings, prices)
    db.execute(select(func.pg_advisory_xact_lock(TRIAGE_BUDGET_LOCK)))
    close_abandoned(db, settings)
    check_limits(db, settings, user, incident.id, reservation)
    request = TriageRequest(
        incident_id=incident.id, requested_by=user.id, provider=provider.name, model=provider.model, prompt_version=PROMPT_VERSION,
        basis_event_id=latest_event_id(db, incident.id), input_sha256=input_digest(triage_input), candidate_ids=candidate_ids,
        reserved_microusd=reservation,
    )
    db.add(request)
    db.commit()  # releases the advisory lock; nothing is held while the provider runs
    return request


def record_result(db: Session, settings: TriageSettings, request: TriageRequest, result: ProviderResult) -> TriageSuggestion:
    """Step 4: validates the output and records the outcome and actual cost. Commits."""
    prices = settings.prices()
    if result.billing_unknown or result.input_tokens is None or result.output_tokens is None:
        cost = request.reserved_microusd  # conservative: the provider may have billed
    else:
        cost = cost_microusd(result.input_tokens, result.output_tokens, prices)  # actual, as reported
    fields: dict = {}
    outcome, category = TriageOutcome.INVALID_OUTPUT, result.error_category
    if result.outcome == "ok":
        try:
            valid = validate_output(result.raw, list(request.candidate_ids))
            outcome, category = TriageOutcome.VALID, None
            fields = {"severity": valid.severity, "rationale": valid.rationale, "next_steps": valid.next_steps,
                      "duplicate_ids": valid.duplicate_ids}
        except TriageValidationError as error:
            category = error.category
    else:
        outcome = TriageOutcome(result.outcome) if result.outcome in {o.value for o in TriageOutcome} else TriageOutcome.PROVIDER_ERROR
    suggestion = TriageSuggestion(
        request_id=request.id, outcome=outcome, error_category=category, input_tokens=result.input_tokens,
        output_tokens=result.output_tokens, cost_microusd=cost, latency_ms=result.latency_ms, **fields,
    )
    db.add(suggestion)
    try:
        db.commit()
    except IntegrityError:
        # The reservation was already closed as abandoned (charged in full); keep that record.
        db.rollback()
        return db.scalars(select(TriageSuggestion).where(TriageSuggestion.request_id == request.id)).one()
    # Identifiers, outcome, tokens and cost only: never incident text or model output.
    logger.info("Triage request %s for incident %s: %s (%s), %s/%s tokens, %s micro-USD", request.id, request.incident_id,
                outcome.value, category, result.input_tokens, result.output_tokens, cost)
    return suggestion


def request_suggestion(db: Session, settings: TriageSettings, provider: TriageProvider, user: User, incident: Incident) -> TriageSuggestion:
    candidates = duplicate_candidates(db, incident, user)
    triage_input = build_input(incident, candidates)
    request = reserve(db, settings, provider, user, incident, triage_input, [cid for cid, _ in candidates])
    try:
        result = provider.suggest(triage_input)  # no transaction or lock is held here
    except Exception as error:  # an unexpected provider failure: no server error, charged in full
        logger.error("Triage request %s: provider raised %s", request.id, type(error).__name__)
        result = ProviderResult(outcome="provider_error", error_category="exception", billing_unknown=True)
    return record_result(db, settings, request, result)


# ---- Decisions -------------------------------------------------------------------------------------------

def default_note(suggestion: TriageSuggestion) -> str:
    steps = "\n".join(f"- {step}" for step in suggestion.next_steps or [])
    return f"AI triage suggestion #{suggestion.id} (reviewed): {suggestion.rationale}" + (f"\nNext steps:\n{steps}" if steps else "")


def decide(db: Session, user: User, suggestion: TriageSuggestion, incident: Incident, decision: str, severity: Severity | None,
           add_internal_note: bool, note: str | None, reason: str | None) -> TriageDecision:
    """Applies a person's decision. The caller has locked the incident row (short, no provider call)."""
    from .comments import add_comment
    from .incidents import apply_incident_changes

    if suggestion.outcome != TriageOutcome.VALID:
        raise TriageConflict("Only a valid suggestion can be accepted or rejected.")
    if db.scalar(select(TriageDecision.id).where(TriageDecision.suggestion_id == suggestion.id)) is not None:
        raise TriageConflict("A decision has already been recorded for this suggestion.")
    if decision == "reject":
        record = TriageDecision(suggestion_id=suggestion.id, decided_by=user.id, decision=TriageDecisionKind.REJECTED, reason=reason)
    else:
        if latest_event_id(db, incident.id) != suggestion.request.basis_event_id:
            raise TriageConflict("The incident changed after this suggestion was made. Request a new suggestion.")
        chosen = severity or suggestion.severity
        # Validate the note first, so an invalid note changes nothing.
        body = clean_comment_body(note if note is not None else default_note(suggestion)) if add_internal_note else None
        event = apply_incident_changes(db, incident, user, {"severity": chosen})
        comment = add_comment(db, incident.id, user, body, CommentVisibility.INTERNAL) if body is not None else None
        db.flush()  # assigns the audit event's id
        edited = chosen != suggestion.severity or (add_internal_note and note is not None)
        record = TriageDecision(
            suggestion_id=suggestion.id, decided_by=user.id,
            decision=TriageDecisionKind.ACCEPTED_WITH_EDITS if edited else TriageDecisionKind.ACCEPTED,
            applied_severity=chosen if event is not None else None,
            resulting_event_id=event.id if event is not None else None,
            note_comment_id=comment.id if comment is not None else None,
            reason=reason,
        )
    db.add(record)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise TriageConflict("A decision has already been recorded for this suggestion.") from None
    return record
