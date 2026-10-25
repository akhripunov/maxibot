import importlib.util
import json
from pathlib import Path
import sys
import urllib.error
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "prepare_coalescing", ROOT / "scripts" / "prepare_coalescing.py"
)
COALESCING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COALESCING)


class PrepareCoalescingTests(unittest.TestCase):
    def test_single_current_sha_waits_for_bounded_window_then_wins(self):
        sha = "a" * 40
        reads = iter([sha, sha])
        sleeps = []
        result = COALESCING.wait_for_quiet_main(
            sha, lambda: next(reads), sleep=sleeps.append, window_seconds=60
        )
        self.assertEqual(result, "WINNER")
        self.assertEqual(sleeps, [60])

    def test_rapid_pushes_coalesce_expensive_prepare_to_latest_sha(self):
        shas = [letter * 40 for letter in "abcd"]
        current = [shas[0]]
        expensive = []

        def advance_to_latest(_seconds):
            current[0] = shas[-1]

        outcomes = []
        for candidate in shas[:-1]:
            outcome = COALESCING.wait_for_quiet_main(
                candidate, lambda: current[0], sleep=advance_to_latest, window_seconds=60
            )
            outcomes.append((candidate, outcome))
            if outcome == "WINNER":
                expensive.append(candidate)
        final = COALESCING.wait_for_quiet_main(
            shas[-1], lambda: current[0], sleep=lambda _: None, window_seconds=60
        )
        outcomes.append((shas[-1], final))
        if final == "WINNER":
            expensive.append(shas[-1])

        self.assertEqual([outcome for _, outcome in outcomes], [
            "SUPERSEDED", "SUPERSEDED", "SUPERSEDED", "WINNER"
        ])
        self.assertEqual(expensive, [shas[-1]])

    def test_new_sha_during_debounce_supersedes_candidate(self):
        old, new = "a" * 40, "b" * 40
        current, sleeps = [old], []

        def advance(_seconds):
            sleeps.append(_seconds)
            current[0] = new

        self.assertEqual(
            COALESCING.wait_for_quiet_main(
                old, lambda: current[0], sleep=advance, window_seconds=60
            ),
            "SUPERSEDED",
        )
        self.assertEqual(sleeps, [60])

    def test_stale_retry_is_rejected_before_wait_or_expensive_work(self):
        old, current = "a" * 40, "b" * 40
        sleeps, expensive = [], []
        outcome = COALESCING.wait_for_quiet_main(
            old, lambda: current, sleep=sleeps.append, window_seconds=60
        )
        if outcome == "WINNER":
            expensive.append(old)
        self.assertEqual(outcome, "SUPERSEDED")
        self.assertEqual(sleeps, [])
        self.assertEqual(expensive, [])

    def test_current_main_lookup_failure_fails_closed(self):
        with self.assertRaises(COALESCING.CoalescingError):
            COALESCING.wait_for_quiet_main(
                "a" * 40,
                lambda: (_ for _ in ()).throw(OSError("offline")),
                sleep=lambda _: None,
                window_seconds=60,
            )

    def test_malformed_github_ref_payload_fails_closed(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"[]"

        with self.assertRaises(COALESCING.CoalescingError):
            COALESCING.read_current_main_sha("redacted-test-token", opener=lambda *_a, **_k: Response())

    def test_github_http_error_fails_closed_without_echoing_credentials(self):
        calls = []

        def reject(*_args, **_kwargs):
            calls.append(1)
            raise urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, None)

        with self.assertRaises(COALESCING.CoalescingError) as raised:
            COALESCING.read_current_main_sha("redacted-test-token", opener=reject)
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertNotIn("redacted-test-token", str(raised.exception))
        self.assertEqual(len(calls), 1)

    def test_main_sha_read_retries_transient_tls_with_shared_budget(self):
        import ssl
        sha = "a" * 40
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"object": {"sha": sha}}).encode()

        def intermittent(*_args, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.URLError(ssl.SSLEOFError("unexpected EOF"))
            return Response()

        budget = COALESCING.RetryBudget(seconds=10)
        result = COALESCING.read_current_main_sha(
            "redacted-test-token", opener=intermittent, budget=budget,
            retry_options={"sleep": lambda _delay: None,
                           "jitter": lambda _low, _high: 0,
                           "emit": lambda _event: None},
        )

        self.assertEqual(result, sha)
        self.assertEqual(len(calls), 2)

    def test_prepare_initializes_one_retry_budget_before_coalescing(self):
        workflow = (ROOT / ".github" / "workflows" / "maxibot-prepare.yml").read_text()
        initialize = workflow.index("Initialize shared retry budget")
        checkout = workflow.index("Checkout exact candidate")
        coalescing = workflow.index("Wait for a quiet main and verify the exact candidate")
        self.assertLess(initialize, checkout)
        self.assertLess(checkout, coalescing)
        self.assertIn("NETWORK_RETRY_BUDGET_STATE", workflow[initialize:checkout])

    def test_prepare_tests_do_not_charge_synthetic_time_to_workflow_budget(self):
        workflow = (ROOT / ".github" / "workflows" / "maxibot-prepare.yml").read_text()
        test_step = workflow[workflow.index("Run V3 exact-candidate required test suite"):]
        self.assertIn("env -u NETWORK_RETRY_BUDGET_STATE python3 -B scripts/maxibot_impact_v3.py", test_step)

    def test_invalid_sha_from_github_ref_fails_closed(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"object":{"sha":"invalid"}}'

        with self.assertRaises(COALESCING.CoalescingError):
            COALESCING.read_current_main_sha("redacted-test-token", opener=lambda *_a, **_k: Response())

    def test_all_expensive_prepare_steps_are_gated_before_execution(self):
        workflow = (ROOT / ".github" / "workflows" / "maxibot-prepare.yml").read_text()
        gate = "steps.coalesce.outputs.result == 'WINNER'"
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'push' }}", workflow)
        self.assertLess(workflow.index("id: coalesce"), workflow.index("actions/setup-python"))
        self.assertLess(workflow.index("id: coalesce"), workflow.index("Run V3 exact-candidate required test suite"))
        for expensive in (
            "Set deterministic Python runtime",
            "Pin Docker build version",
            "Plan MAXIBOT V3 from immutable production evidence",
            "Run V3 exact-candidate required test suite",
            "Enable BuildKit cross-run cache",
            "Build and verify canonical linux/amd64 candidate image",
            "Rehearse archive import in private Docker/containerd",
        ):
            start = workflow.index(expensive)
            self.assertIn(gate, workflow[start:start + 250], expensive)
        auto = (ROOT / ".github" / "workflows" / "maxibot-auto-preflight.yml").read_text()
        auto_events = auto.split("on:", 1)[1].split("permissions:", 1)[0]
        self.assertNotIn("workflow_run:", auto_events)
        self.assertIn("if: ${{ false }}", auto)
        publish_if = workflow[workflow.index("needs: prepare"):workflow.index("runs-on: ubuntu-latest", workflow.index("needs: prepare"))]
        self.assertIn("always() && github.ref == 'refs/heads/main'", publish_if)
        self.assertIn("needs.prepare.outputs.coalescing != 'SUPERSEDED'", publish_if)
        self.assertIn("needs.prepare.result != 'cancelled'", publish_if)
        dispatch_validator = (ROOT / "scripts" / "validate_release_dispatch.py").read_text()
        self.assertIn("if main_sha != sha:", dispatch_validator)
        self.assertIn("current origin/main SHA", dispatch_validator)


if __name__ == "__main__":
    unittest.main()
