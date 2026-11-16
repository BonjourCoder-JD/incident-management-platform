"""Release gate (Milestone 6 Phase E): may the images of one commit be published?

    gh api "repos/OWNER/REPO/actions/runs?head_sha=SHA&per_page=100" | python3 deploy/release_gate.py SHA

Reads the GitHub API's workflow runs for the commit and prints `decision=publish|wait|refuse` and a reason.
Every required workflow must have run for EXACTLY this commit, as a push to main in this repository, and
its latest such run must have succeeded:
- publish: all required workflows succeeded;
- wait: one has not run yet or has not finished (its completion triggers the Release workflow again);
- refuse: one failed, was cancelled, was skipped or timed out (a re-run that succeeds triggers it again).
Exit status 0 for publish and wait, 1 for refuse, 2 for bad input. Standard library only.
"""
import json
import re
import sys

REQUIRED = ("CI", "Security scans")


def decide(runs: list[dict], sha: str, repository: str | None = None) -> tuple[str, str]:
    relevant = [
        run for run in runs
        if run.get("head_sha") == sha and run.get("event") == "push" and run.get("head_branch") == "main"
        and (repository is None or (run.get("head_repository") or {}).get("full_name") == repository)
    ]
    reasons = []
    waiting = refused = False
    for name in REQUIRED:
        candidates = [run for run in relevant if run.get("name") == name]
        if not candidates:
            waiting = True
            reasons.append(f"{name}: no run yet")
            continue
        latest = max(candidates, key=lambda run: (run.get("run_number", 0), run.get("run_attempt", 1), run.get("id", 0)))
        if latest.get("status") != "completed":
            waiting = True
            reasons.append(f"{name}: {latest.get('status')}")
        elif latest.get("conclusion") != "success":
            refused = True
            reasons.append(f"{name}: {latest.get('conclusion')}")
        else:
            reasons.append(f"{name}: success")
    decision = "refuse" if refused else "wait" if waiting else "publish"
    return decision, "; ".join(reasons)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or not re.fullmatch(r"[0-9a-f]{40}", argv[1]):
        print("usage: release_gate.py <40-character commit SHA> [owner/repo] < runs.json", file=sys.stderr)
        return 2
    try:
        runs = json.load(sys.stdin)["workflow_runs"]
    except (ValueError, KeyError, TypeError):
        print("release_gate: input is not a workflow-runs response", file=sys.stderr)
        return 2
    decision, reason = decide(runs, argv[1], argv[2] if len(argv) == 3 else None)
    print(f"decision={decision}")
    print(f"reason={reason}")
    return 1 if decision == "refuse" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
