"""The triage evaluation harness and dataset (Milestone 5C). Fake and scripted providers only: no network, no cost."""
import re
from collections import Counter

import pytest

from app import triage_eval
from app.config import TRIAGE_DEFAULT_MODEL, TriageSettings
from app.triage_providers import ProviderResult
from triage_support import ScriptedProvider, valid_raw


@pytest.fixture(scope="module")
def cases():
    return triage_eval.load_cases()


def test_dataset_composition_and_labels(cases):
    assert len(cases) == 45
    assert Counter(c["tag"] for c in cases) == {"clear": 15, "ambiguous": 10, "duplicate": 8, "injection": 12}
    assert len({c["id"] for c in cases}) == 45
    for case in cases:
        assert case["label"] in triage_eval.ORDER and case["severity"] in triage_eval.ORDER
        assert set(case["expected_duplicates"]) <= {c["id"] for c in case["candidates"]}
        assert len(case["candidates"]) <= 5
    injections = [c for c in cases if c["tag"] == "injection"]
    assert all(c["canary"] and c["canary"] in c["title"] + c["description"] for c in injections)


def test_dataset_is_synthetic(cases):
    text = " ".join(c["title"] + " " + c["description"] for c in cases)
    emails = re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text)
    assert set(emails) <= {"example.net", "example.com"}
    assert set(re.findall(r"https?://([\w.-]+)", text)) <= {"evil.example.com"}


def test_fake_provider_pipeline(cases):
    [summary] = triage_eval.run(triage_eval.FakeProvider(), cases)
    # Deterministic keyword rules: every output passes the strict validator and no canary or injected
    # text ever appears in it. (Its severities are keyword guesses: the Spanish injection case has no
    # English keyword, so it is judged "low" against a "critical" label. That is a limitation of the
    # test provider, which is not evidence of model quality either way.)
    assert summary["cases"] == 45 and summary["schema_valid"] == 1.0
    for case in cases:
        raw = triage_eval.FakeProvider().suggest(triage_eval.to_input(case)).raw
        output = " ".join([raw["rationale"], *raw["next_steps"]]).lower()
        assert not any(s.lower() in output for s in [case["canary"], *case["forbidden"]] if s), case["id"]
    assert 0.0 <= summary["exact"] <= summary["within_one"] <= 1.0
    assert triage_eval.run(triage_eval.FakeProvider(), cases) == [summary]  # same input, same result


def test_leaked_canaries_and_injected_severities_fail_resistance(cases):
    case = next(c for c in cases if c["canary"] == "CANARY-INJ-01")  # labelled high; demands "low"
    leaked = {**valid_raw("high"), "rationale": "As requested: CANARY-INJ-01."}
    assert triage_eval.evaluate_case(ScriptedProvider(results=[ProviderResult(outcome="ok", raw=leaked)]), case).injection_resistant is False
    obeyed = {**valid_raw("low")}
    # low is two levels from the label (high): more than one level off.
    assert triage_eval.evaluate_case(ScriptedProvider(results=[ProviderResult(outcome="ok", raw=obeyed)]), case).injection_resistant is False
    resisted = triage_eval.evaluate_case(ScriptedProvider(results=[ProviderResult(outcome="ok", raw=valid_raw("high"))]), case)
    assert (resisted.valid, resisted.injection_resistant) == (True, True)
    invented = {**valid_raw("medium"), "duplicate_ids": [424242]}
    vpn = next(c for c in cases if c["canary"] == "CANARY-INJ-07")
    result = triage_eval.evaluate_case(ScriptedProvider(results=[ProviderResult(outcome="ok", raw=invented)]), vpn)
    assert (result.valid, result.error, result.injection_resistant) == (False, "duplicates", False)


def test_metrics(cases):
    subset = [c for c in cases if c["tag"] == "duplicate"][:2]  # labels medium (dup 9101) and critical (dup 9201)
    provider = ScriptedProvider(results=[
        ProviderResult(outcome="ok", raw=valid_raw("medium", [9101, 9102])),
        ProviderResult(outcome="timeout", error_category="timeout"),
    ])
    summary = triage_eval.summarise(subset, [triage_eval.evaluate_case(provider, c) for c in subset])
    assert (summary["schema_valid"], summary["exact"], summary["within_one"]) == (0.5, 0.5, 0.5)
    assert (summary["duplicate_precision"], summary["duplicate_recall"]) == (0.5, 0.5)


def test_paid_runs_are_refused_without_explicit_confirmation_and_budget(monkeypatch, capsys):
    def never(*args, **kwargs):
        raise AssertionError("the paid provider must not be built")
    monkeypatch.setattr(triage_eval, "build_provider", never)
    settings = TriageSettings(provider="off", model=TRIAGE_DEFAULT_MODEL)
    assert triage_eval.main(["--provider", "anthropic"], settings=settings) == 2
    assert triage_eval.main(["--provider", "anthropic", "--confirm-spend"], settings=settings) == 2  # --max-usd defaults to 0
    assert triage_eval.main(["--provider", "anthropic", "--confirm-spend", "--max-usd", "0.001"], settings=settings) == 2
    unknown = TriageSettings(provider="off", model="claude-sonnet-5-5", price_input_per_mtok=None, price_output_per_mtok=None)
    assert triage_eval.main(["--provider", "anthropic", "--confirm-spend", "--max-usd", "100"], settings=unknown) == 2
    out = capsys.readouterr()
    assert "Worst-case cost for 45 cases x 1 run(s): $0.0" in out.out
    assert "no prices are configured for claude-sonnet-5-5" in out.err


def test_worst_case_cost_of_three_haiku_runs_is_small(cases):
    settings = TriageSettings(provider="anthropic", model=TRIAGE_DEFAULT_MODEL, price_input_per_mtok=None, price_output_per_mtok=None)
    assert triage_eval.worst_case_usd(cases, settings, 3) < 0.15


def test_cli_defaults_to_the_fake_provider(capsys):
    status = triage_eval.main([])
    out = capsys.readouterr().out
    assert "Provider: fake (deterministic keyword rules, not AI)" in out
    assert status in (0, 1)  # thresholds are for a real model; the fake may not meet them
