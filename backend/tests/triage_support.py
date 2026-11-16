"""Test doubles for AI triage. No test ever constructs a real (paid) provider client."""
import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from app.triage_providers import ProviderResult, TriageInput


def valid_raw(severity="high", duplicate_ids=()):
    return {"severity": severity, "rationale": "Credential theft affecting a privileged account.",
            "next_steps": ["Reset the password.", "Review sign-in logs."], "duplicate_ids": list(duplicate_ids)}


@dataclass
class ScriptedProvider:
    """Returns prepared results in order (repeating the last); can sleep or raise to simulate slow or crashing calls."""
    results: list = field(default_factory=lambda: [ProviderResult(outcome="ok", raw=valid_raw(), input_tokens=900, output_tokens=150)])
    sleep: float = 0.0
    on_call: object = None  # callable run during the call (while no transaction should be open)
    name: str = "scripted"
    model: str = "scripted-model"
    label: str = "Scripted test provider"
    inputs: list = field(default_factory=list)

    def suggest(self, triage_input: TriageInput) -> ProviderResult:
        self.inputs.append(triage_input)
        if self.on_call is not None:
            self.on_call()
        if self.sleep:
            time.sleep(self.sleep)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, BaseException):
            raise result
        return result


class FakeMessages:
    """Stands in for the SDK's `client.messages` and records every request."""

    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def sdk_response(payload=None, stop_reason="end_turn", text=None, usage=(1800, 200)):
    body = text if text is not None else json.dumps(payload if payload is not None else valid_raw())
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]) if usage else None,
        content=[SimpleNamespace(type="text", text=body)],
    )
