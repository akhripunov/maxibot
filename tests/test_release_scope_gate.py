import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_scope_gate import evaluate_scope

BASE, CANDIDATE = "a" * 40, "b" * 40


class ReleaseScopeGateTests(unittest.TestCase):
    def decide(self, paths=("app/bot/menu.py",), **overrides):
        values = dict(baseline_sha=BASE, candidate_sha=CANDIDATE,
                      mode="TASK_SCOPED_AUTO", authorization_ref="linear:MAX-123",
                      task_scope="Fix bot menu destination", authorized_prefixes=("app/bot",),
                      changed_paths=paths, risk_attestation="LOW")
        values.update(overrides)
        return evaluate_scope(**values)

    def test_bounded_low_risk_task_scope_passes(self):
        result = self.decide()
        self.assertEqual(result.status, "AUTO_RELEASE_ALLOWED")

    def test_scope_expansion_requires_owner(self):
        result = self.decide(("app/bot/menu.py", "docs/other.md"))
        self.assertEqual(result.status, "OWNER_DECISION_REQUIRED")
        self.assertIn("scope_expansion", result.reason)

    def test_release_infrastructure_is_always_owner_only(self):
        for path in (".github/workflows/deploy.yml", "scripts/release_prod.sh"):
            with self.subTest(path=path):
                self.assertEqual(self.decide((path,), authorized_prefixes=(".",)).status,
                                 "OWNER_DECISION_REQUIRED")

    def test_migration_security_ads_and_unknown_risk_fail_closed(self):
        for path in ("alembic/versions/001.py", "security/policy.py", "app/ads/write.py"):
            with self.subTest(path=path):
                self.assertEqual(self.decide((path,), authorized_prefixes=(".",)).status,
                                 "OWNER_DECISION_REQUIRED")
        self.assertEqual(self.decide(risk_attestation="UNKNOWN").status,
                         "OWNER_DECISION_REQUIRED")

    def test_missing_owner_evidence_or_exact_sha_fails_closed(self):
        self.assertEqual(self.decide(authorization_ref="").status, "OWNER_DECISION_REQUIRED")
        self.assertEqual(self.decide(mode="EXACT_SHA").status, "OWNER_DECISION_REQUIRED")
        self.assertEqual(self.decide(candidate_sha=BASE).status, "OWNER_DECISION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
