import importlib.util
from pathlib import Path
import urllib.error
import unittest


ROOT = Path(__file__).resolve().parents[1]
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
        def reject(*_args, **_kwargs):
            raise urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, None)

        with self.assertRaises(COALESCING.CoalescingError) as raised:
            COALESCING.read_current_main_sha("redacted-test-token", opener=reject)
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertNotIn("redacted-test-token", str(raised.exception))

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
        gate = "needs.coalesce.outputs.result == 'WINNER'"
        coalesce = workflow[workflow.index("  coalesce:"):workflow.index("  prepare:")]
        prepare = workflow[workflow.index("  prepare:"):workflow.index("  publish:")]
        self.assertIn("runs-on: [self-hosted, macOS, maxibot-production]", coalesce)
        self.assertIn("permissions:\n      contents: read", coalesce)
        self.assertNotIn("GHCR", coalesce)
        self.assertNotIn("LINEAR_API_KEY", coalesce)
        self.assertIn("needs: coalesce", prepare)
        self.assertIn("if: github.ref == 'refs/heads/main' && needs.coalesce.result == 'success'", prepare)
        self.assertNotIn("python3 scripts/prepare_coalescing.py", prepare)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'push' }}", workflow)
        self.assertLess(workflow.index("  coalesce:"), workflow.index("actions/setup-python"))
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
        self.assertIn("github.event.workflow_run.head_sha == github.sha", (
            ROOT / ".github" / "workflows" / "maxibot-auto-preflight.yml"
        ).read_text())
        publish_if = workflow[workflow.index("  publish:"):workflow.index("runs-on: ubuntu-latest", workflow.index("  publish:"))]
        self.assertIn("always() && github.ref == 'refs/heads/main'", publish_if)
        self.assertIn("needs.coalesce.outputs.result == 'WINNER'", publish_if)
        self.assertIn("needs.coalesce.result == 'success'", publish_if)
        self.assertIn("needs.prepare.result != 'cancelled'", publish_if)
        self.assertIn("options: [prepared, ghcr-oci, archive]", (ROOT / ".github" / "workflows" / "maxibot-deploy.yml").read_text())
        dispatch_validator = (ROOT / "scripts" / "validate_release_dispatch.py").read_text()
        self.assertIn("if main_sha != sha:", dispatch_validator)
        self.assertIn("current origin/main SHA", dispatch_validator)


if __name__ == "__main__":
    unittest.main()
