"""Regression tests for deploy/release_gate.py (standard library only):

    python3 -m unittest discover -s deploy/tests -p "test_*.py"
"""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import release_gate  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40
REPO = "owner/incident-management-platform"


def run(name, conclusion="success", status="completed", sha=SHA, event="push", branch="main", number=1, attempt=1, repo=REPO):
    return {"name": name, "status": status, "conclusion": conclusion if status == "completed" else None, "head_sha": sha,
            "event": event, "head_branch": branch, "run_number": number, "run_attempt": attempt, "id": number * 10 + attempt,
            "head_repository": {"full_name": repo}}


class ReleaseGate(unittest.TestCase):
    def decide(self, *runs):
        return release_gate.decide(list(runs), SHA, REPO)[0]

    def test_both_required_workflows_succeeded(self):
        self.assertEqual(self.decide(run("CI"), run("Security scans")), "publish")

    def test_a_failed_required_workflow_refuses(self):
        self.assertEqual(self.decide(run("CI"), run("Security scans", "failure")), "refuse")
        self.assertEqual(self.decide(run("CI", "failure"), run("Security scans")), "refuse")

    def test_skipped_cancelled_or_timed_out_refuses(self):
        for conclusion in ("skipped", "cancelled", "timed_out", "action_required", "neutral", None):
            self.assertEqual(self.decide(run("CI"), run("Security scans", conclusion)), "refuse", conclusion)

    def test_an_unfinished_or_missing_workflow_waits(self):
        self.assertEqual(self.decide(run("CI"), run("Security scans", status="in_progress")), "wait")
        self.assertEqual(self.decide(run("CI", status="queued"), run("Security scans")), "wait")
        self.assertEqual(self.decide(run("CI")), "wait")
        self.assertEqual(self.decide(), "wait")

    def test_a_failure_outweighs_a_workflow_still_running(self):
        self.assertEqual(self.decide(run("CI", "failure"), run("Security scans", status="in_progress")), "refuse")

    def test_only_runs_for_exactly_this_commit_count(self):
        self.assertEqual(self.decide(run("CI"), run("Security scans", sha=OTHER)), "wait")

    def test_pull_requests_schedules_other_branches_and_forks_do_not_count(self):
        for other in (run("Security scans", event="pull_request"), run("Security scans", event="schedule"),
                      run("Security scans", branch="feature"), run("Security scans", repo="fork/incident-management-platform")):
            self.assertEqual(self.decide(run("CI"), other), "wait")

    def test_the_latest_run_decides(self):
        self.assertEqual(self.decide(run("CI", "failure", number=1), run("CI", number=2), run("Security scans")), "publish")
        self.assertEqual(self.decide(run("CI", number=1), run("CI", "failure", number=2), run("Security scans")), "refuse")
        self.assertEqual(self.decide(run("CI", "failure", attempt=1), run("CI", attempt=2), run("Security scans")), "publish")

    def test_unrelated_workflows_are_ignored(self):
        self.assertEqual(self.decide(run("CI"), run("Security scans"), run("Release images", "failure")), "publish")

    def test_command_line(self):
        def call(argv, data):
            sys.stdin = io.StringIO(data)
            out = io.StringIO()
            try:
                with redirect_stdout(out):
                    code = release_gate.main(argv)
            finally:
                sys.stdin = sys.__stdin__
            return code, out.getvalue()
        runs = json.dumps({"workflow_runs": [run("CI"), run("Security scans")]})
        self.assertEqual(call(["gate", SHA, REPO], runs), (0, "decision=publish\nreason=CI: success; Security scans: success\n"))
        failed = json.dumps({"workflow_runs": [run("CI"), run("Security scans", "failure")]})
        self.assertEqual(call(["gate", SHA, REPO], failed)[0], 1)
        self.assertEqual(call(["gate", "not-a-sha"], runs)[0], 2)
        self.assertEqual(call(["gate", SHA], "not json")[0], 2)


if __name__ == "__main__":
    unittest.main()
