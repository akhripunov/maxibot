from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = (ROOT / "AGENTS.md").read_text()
ORCHESTRATION = (ROOT / "docs/ORCHESTRATION_PROTOCOL.md").read_text()
ROLE_REGISTRY = (ROOT / "docs/ROLE_REGISTRY.md").read_text()
ARCHITECT_CONTEXT = (ROOT / "docs/ARCHITECT_SOL_CONTEXT_PACK.md").read_text()
RELEASE_AUTH = (ROOT / "docs/RELEASE_AUTHORIZATION.md").read_text()
RELEASE_AUTOMATION = (ROOT / "docs/RELEASE_AUTOMATION.md").read_text()
DEPLOY_WORKFLOW = (ROOT / ".github/workflows/maxibot-native-release.yml").read_text()


class AgentPolicyContractTests(unittest.TestCase):
    def test_architect_is_external_chatgpt_and_dev_owns_approved_execution(self) -> None:
        self.assertIn("ARCHITECT / ARCHITECT SOL", ROLE_REGISTRY)
        self.assertIn("external ChatGPT project architect", ROLE_REGISTRY)
        self.assertIn("ARCHITECT SOL is a compatibility alias", ROLE_REGISTRY)
        self.assertIn("DEV is the primary autonomous technical implementer", ROLE_REGISTRY)
        self.assertIn("OWNER approval", ROLE_REGISTRY)
        self.assertNotIn("ARCHITECT_CODEX_REQUIRED = YES", AGENTS + ROLE_REGISTRY + ORCHESTRATION)
        self.assertNotIn("| ARCHITECT CODEX |", ROLE_REGISTRY)
        self.assertIn("primary autonomous technical implementer and task owner", ARCHITECT_CONTEXT)
        self.assertIn("Optional specialists and canonical QA", ARCHITECT_CONTEXT)

    def test_routine_dev_work_has_no_architect_codex_chat_prerequisite(self) -> None:
        active_policy = AGENTS + ROLE_REGISTRY + ORCHESTRATION
        self.assertIn("DEV proceeds without a separate ARCHITECT CODEX chat", active_policy)
        self.assertIn("escalate only for a real product or architecture conflict", active_policy)
        self.assertNotIn("ARCHITECT CODEX must resolve", active_policy)
        self.assertNotIn("ARCHITECT_CODEX_REQUIRED", active_policy)

    def test_role_specific_linear_context_is_canonical_and_scoped(self) -> None:
        contexts = ROLE_REGISTRY.split("## Canonical Linear context by role", 1)[1]
        expected = {
            "ARCHITECT / ARCHITECT SOL": (
                "maxibot-project-context-agents-1a98f6000c28",
                "arhitektor-handoff-bb75d67e4a95",
            ),
            "DEV": (
                "maxibot-project-context-agents-1a98f6000c28",
                "maxibot-workers-97f568bf84b1",
            ),
            "WEBDEV": (
                "maxibot-project-context-agents-1a98f6000c28",
                "arhitektor-handoff-bb75d67e4a95",
            ),
            "ДИРЕКТОЛОГ": (
                "maxibot-project-context-agents-1a98f6000c28",
                "maxibot-workers-97f568bf84b1",
            ),
            "QA": ("maxibot-project-context-agents-1a98f6000c28", "qa-7a34fcf9286c"),
            "SYSADMIN": (
                "maxibot-workers-97f568bf84b1",
                "pravila-reliza-maxibot-825471a52588",
            ),
        }
        for role, slugs in expected.items():
            row = next(line for line in contexts.splitlines() if line.startswith(f"| {role} |"))
            for slug in slugs:
                self.assertIn(slug, row, f"{role} missing canonical Linear context {slug}")
        self.assertIn("If Linear is unavailable", contexts)
        self.assertIn("NOT_VERIFIED", contexts)

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
        for role in ("DEV", "WEBDEV", "ДИРЕКТОЛОГ", "QA", "SYSADMIN"):
            self.assertTrue(role in ORCHESTRATION, f"missing registered role: {role}")
        self.assertNotIn("ARCHITECT_CODEX_REQUIRED", ORCHESTRATION)


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
        self.assertNotIn("task_scoped_auto", DEPLOY_WORKFLOW.lower())
        self.assertIn("name: production", DEPLOY_WORKFLOW.lower())
        self.assertIn("github.event_name == 'workflow_dispatch'", lower)
        self.assertIn("operation == 'rollback'", lower)
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

    def test_github_access_runbook_is_error_diagnostic_not_routine_gate(self) -> None:
        access_docs = (AGENTS, ORCHESTRATION, ROLE_REGISTRY, RELEASE_AUTOMATION)
        runbook_url = "https://linear.app/bestuzhev/document/github-dostup-7970682d1b80"
        for content in access_docs:
            self.assertIn(runbook_url, content)
        self.assertIn("Healthy access does not require that Linear read", " ".join(AGENTS.split()))
        self.assertIn("Normal successful GitHub/GHCR access does not require", ORCHESTRATION)
        self.assertIn(
            "Successful GitHub, GHCR, Git CLI, and GitHub Actions operations do not require reading",
            " ".join(ROLE_REGISTRY.split()),
        )
        self.assertIn("On an actual GitHub/GHCR transport", " ".join(RELEASE_AUTOMATION.split()))
        self.assertIn("Read the runbook once per incident", ORCHESTRATION)
        self.assertIn("same unchanged failure", ORCHESTRATION)
        self.assertIn("GITHUB_ACCESS_RUNBOOK_NOT_VERIFIED", ORCHESTRATION)
        self.assertIn("GITHUB_ACCESS_BLOCKED", ORCHESTRATION)
        self.assertIn("read back remote SHA", ORCHESTRATION)
        combined = AGENTS + ORCHESTRATION + ROLE_REGISTRY + RELEASE_AUTOMATION
        for stale_rule in (
            "before the first relevant access",
            "before relevant access",
            "Mandatory GitHub/GHCR access preflight",
        ):
            self.assertNotIn(stale_rule, combined)
        for prohibited in ("create/rotate credentials", "relax SSH/TLS checks", "VPN/DNS"):
            self.assertIn(prohibited, ORCHESTRATION)
        self.assertIn("single Poller", AGENTS)
        self.assertIn("Worker 512 MiB", AGENTS)
        self.assertIn("PostgreSQL/Redis/volumes", AGENTS)
        self.assertIn("backup/rollback", AGENTS)
        self.assertIn("exact SHA/digest", AGENTS)
        self.assertIn("frozen paths", AGENTS)


if __name__ == "__main__":
    unittest.main()
