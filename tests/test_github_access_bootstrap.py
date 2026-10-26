"""Guard the MAXIBOT Codex GitHub-access runbook bootstrap contract.

The Linear document remains the sole operational runbook; this test prevents
accidental removal of its mandatory discovery and incident-handling rules.
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = "https://linear.app/bestuzhev/document/github-dostup-7970682d1b80"
BOOTSTRAP = (
    "AGENTS.md",
    "docs/ROLE_REGISTRY.md",
    "docs/ORCHESTRATION_PROTOCOL.md",
)


@pytest.mark.parametrize("relative_path", BOOTSTRAP)
def test_github_access_document_link_is_mandatory_in_all_bootstraps(relative_path: str):
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert RUNBOOK in text
    assert "mandatory" in text.lower()
    assert "linear" in text.lower()
    assert "context compaction" in text.lower()
    assert "access" in text.lower()


def test_agents_requires_live_document_and_disallows_false_auth_revocation():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "before the first relevant access operation" in text
    assert "gh auth status" in text
    assert "not sufficient proof of revocation" in text
    assert "GITHUB_ACCESS_RUNBOOK_NOT_VERIFIED" in text
    assert "remote exact SHA" in text
    assert "without the applicable OWNER authorization" in text


def test_protocol_separates_read_failure_from_confirmed_access_blocker():
    text = (ROOT / "docs/ORCHESTRATION_PROTOCOL.md").read_text(encoding="utf-8")
    for required in (
        "GITHUB_ACCESS_RUNBOOK_READ",
        "GITHUB_ACCESS_RUNBOOK_NOT_VERIFIED",
        "GITHUB_ACCESS_BLOCKED",
        "DNS",
        "TLS/timeout",
        "existing runs",
        "No forced pushes",
        "another project's credentials",
    ):
        assert required in text


def test_registry_applies_to_actual_github_operators_not_all_roles():
    text = (ROOT / "docs/ROLE_REGISTRY.md").read_text(encoding="utf-8")
    assert "Any role performing GitHub" in text
    assert "DEV and SYSADMIN" in text
    assert "current" in text.lower()
