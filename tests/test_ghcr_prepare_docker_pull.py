import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "ghcr_prepare_cache", ROOT / "scripts" / "ghcr_prepare_cache.py"
)
CACHE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CACHE)


class GhcrDockerPullTests(unittest.TestCase):
    def test_tls_failure_retries_digest_pull_with_same_budget(self):
        from release_network_retry import RetryBudget

        calls = []
        reference = "ghcr.io/akhripunov/maxibot@sha256:" + "a" * 64

        def run(args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise subprocess.CalledProcessError(
                    1, args, stderr="net/http: TLS handshake timeout"
                )
            return subprocess.CompletedProcess(args, 0)

        CACHE._pull_image_by_digest(
            "/usr/bin/docker", reference, {}, budget=RetryBudget(seconds=5),
            run_command=run, retry_options={
                "sleep": lambda _delay: None,
                "jitter": lambda _low, _high: 0,
                "emit": lambda _event: None,
            },
        )

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[0][1:3] == ["pull", reference] for call in calls))
        self.assertAlmostEqual(calls[0][1]["timeout"], 5, places=3)

    def test_authentication_failure_is_not_retried(self):
        from release_network_retry import RetryBudget

        calls = []

        def reject(args, **_kwargs):
            calls.append(args)
            raise subprocess.CalledProcessError(1, args, stderr="denied: requested access to the resource is denied")

        with self.assertRaises(CACHE.ArtifactError):
            CACHE._pull_image_by_digest(
                "/usr/bin/docker", "ghcr.io/akhripunov/maxibot@sha256:" + "a" * 64,
                {}, budget=RetryBudget(seconds=5), run_command=reject,
                retry_options={"sleep": lambda _delay: None,
                               "emit": lambda _event: None},
            )

        self.assertEqual(len(calls), 1)

    def test_each_pull_attempt_timeout_stays_inside_shared_budget_deadline(self):
        from release_network_retry import RetryBudget

        now = [100.0]
        clock = lambda: now[0]
        timeouts = []
        reference = "ghcr.io/akhripunov/maxibot@sha256:" + "a" * 64

        def run(args, **kwargs):
            timeouts.append(kwargs["timeout"])
            if len(timeouts) == 1:
                now[0] += 4
                raise subprocess.CalledProcessError(1, args, stderr="TLS handshake timeout")
            return subprocess.CompletedProcess(args, 0)

        CACHE._pull_image_by_digest(
            "/usr/bin/docker", reference, {}, budget=RetryBudget(seconds=5, clock=clock),
            run_command=run, clock=clock,
            retry_options={"clock": clock, "sleep": lambda _delay: None,
                           "jitter": lambda _low, _high: 0,
                           "emit": lambda _event: None},
        )

        self.assertEqual(timeouts[0], 5)
        self.assertLessEqual(timeouts[1], 1)


if __name__ == "__main__":
    unittest.main()
