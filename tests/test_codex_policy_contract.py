from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = (ROOT / "AGENTS.md").read_text()
ORCHESTRATION = (ROOT / "docs/ORCHESTRATION_PROTOCOL.md").read_text()
RELEASE_AUTH = (ROOT / "docs/RELEASE_AUTHORIZATION.md").read_text()
RELEASE_AUTOMATION = (ROOT / "docs/RELEASE_AUTOMATION.md").read_text()
DEPLOY_WORKFLOW = (ROOT / ".github/workflows/maxibot-deploy.yml").read_text()


class AgentPolicyContractTests(unittest.TestCase):
    def test_root_agents_is_a_compact_map_to_maxibot_canonical_contracts(self) -> None:
        self.assertLessEqual(len(AGENTS.splitlines()), 100)
        for path in (
            "docs/ROLE_REGISTRY.md",
            "docs/ORCHESTRATION_PROTOCOL.md",
            "docs/RELEASE_AUTHORIZATION.md",
            "docs/RELEASE_AUTOMATION.md",
            "docs/frozen_paths.yaml",
            "QA_PROCESS.md",
            "QA_ROLE.md",
            "docs/ARCHITECT_SOL_CONTEXT_PACK.md",
            "tests/regression/test_single_poller_safety.py",
        ):
            self.assertTrue(path in AGENTS, f"missing canonical source: {path}")


    def test_autonomy_keeps_owner_only_boundaries_and_finishes_approved_work(self) -> None:
        lower = ORCHESTRATION.lower()
        for phrase in ("reversible", "owner", "tests", "review", "block"):
            self.assertTrue(phrase in lower, f"missing policy term: {phrase}")
        for term in ("advertising", "owner_only", "secrets"):
            self.assertTrue(term in AGENTS.lower(), f"missing safety rule: {term}")


    def test_new_chat_role_is_explicit_and_does_not_follow_chat_title(self) -> None:
        lower = ORCHESTRATION.lower()
        for phrase in ("role=", "do not infer the role from chat", "with no\nselected role"):
            self.assertTrue(phrase in lower, f"missing role bootstrap rule: {phrase}")
        for role in ("ARCHITECT CODEX", "DEV", "WEBDEV", "ДИРЕКТОЛОГ", "QA", "SYSADMIN"):
            self.assertTrue(role in ORCHESTRATION, f"missing registered role: {role}")


    def test_project_codex_defaults_are_luna_medium_for_primary_and_subagents(self) -> None:
        config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
        self.assertEqual(config["model"], "gpt-6-luna")
        self.assertEqual(config["model_reasoning_effort"], "medium")
        self.assertIs(config["agents"]["enabled"], True)
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual(config["agents"]["default_subagent_reasoning_effort"], "medium")


    def test_advertising_and_frozen_path_negative_scenarios_fail_closed(self) -> None:
        for phrase, content in (
            ("OWNER_ONLY", AGENTS),
            ("docs/frozen_paths.yaml", AGENTS),
            ("scripts/frozen_path_guard.sh", AGENTS),
            ("FROZEN_PATH_REGRESSION", AGENTS),
        ):
            self.assertTrue(phrase in content, f"missing frozen/ads gate: {phrase}")
        self.assertTrue("ads" in RELEASE_AUTH.lower(), "release authorization must exclude ads writes")
        self.assertTrue("ads" in RELEASE_AUTOMATION.lower(), "release automation must exclude ads writes")


    def test_release_authorization_is_maxibot_scoped_and_apply_stays_gated(self) -> None:
        lower = (AGENTS + RELEASE_AUTH + RELEASE_AUTOMATION + DEPLOY_WORKFLOW).lower()
        self.assertIn("maxibot", lower)
        self.assertIn("exact-sha", lower)
        self.assertIn("task_scoped_auto", lower)
        self.assertIn("ready_for_owner", lower)
        self.assertIn("approve_production", lower)
        self.assertIn("gpoint", AGENTS.lower())
        self.assertTrue("never apply gpoint release procedures or authorization to maxibot" in lower)
        self.assertTrue("or maxibot release procedures or authorization to gpoint" in lower)


    def test_single_poller_and_mobile_real_device_contracts_remain_explicit(self) -> None:
        lower = AGENTS.lower()
        self.assertTrue("only the vps worker" in lower, "single production poller identity missing")
        self.assertTrue("max_run_polling=false" in lower, "API/local polling off rule missing")
        self.assertTrue("max_run_polling=true" in lower, "worker polling on rule missing")
        self.assertTrue("real-device" in lower or "real device" in lower)
        self.assertTrue("android" in lower, "Android real-device gate missing")
        self.assertTrue("unit tests" in lower, "unit-only production readiness block missing")
        self.assertTrue("research/maxibot_single_poller_runbook.md" not in lower, "broken runbook link remains")

    def test_single_poller_sources_do_not_reference_missing_runbook(self) -> None:
        audit = (ROOT / "qa/TEST_AUDIT.md").read_text().lower()
        self.assertIn("tests/regression/test_single_poller_safety.py", audit)
        self.assertIn("docs/architect_sol_context_pack.md", audit)
        self.assertNotIn("research/maxibot_single_poller_runbook.md", audit)


if __name__ == "__main__":
    unittest.main()
