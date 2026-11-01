"""Guard the MAXIBOT GitHub-access incident diagnostics contract.

The current Linear runbook is consulted on a real GitHub/GHCR access failure,
not during healthy ordinary tasks. Safety and bounded recovery remain required.
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


@pytest.mark.parametrize(
    ("relative_path", "healthy_access_rule"),
    (
        ("AGENTS.md", "not a prerequisite for ordinary MAXIBOT work"),
        ("docs/ROLE_REGISTRY.md", "operations do not require\nreading"),
        ("docs/ORCHESTRATION_PROTOCOL.md", "access does not require reading the Linear"),
    ),
)
def test_github_access_link_is_for_incident_diagnostics_not_routine_gate(
    relative_path: str, healthy_access_rule: str
):
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    assert RUNBOOK in text
    assert healthy_access_rule in text
    assert "diagnostic" in text.lower()
    assert "DNS" in text
    assert "TLS" in text


def test_agents_preserves_safe_access_failure_recovery():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "gh auth status" in text
    assert "does not prove credential revocation" in text
    assert "GITHUB_ACCESS_RUNBOOK_NOT_VERIFIED" in text
    assert "remote exact SHA" in text
    assert "without applicable OWNER authorization" in text
    assert "Read the runbook once for the current incident" in text
    assert "Never force-push" in text
    assert "before the first relevant access operation" not in text


def test_protocol_separates_read_failure_from_confirmed_access_blocker():
    text = (ROOT / "docs/ORCHESTRATION_PROTOCOL.md").read_text(encoding="utf-8")
    for required in (
        "GITHUB_ACCESS_RUNBOOK_READ",
        "GITHUB_ACCESS_RUNBOOK_NOT_VERIFIED",
        "GITHUB_ACCESS_BLOCKED",
        "DNS",
        "TLS",
        "existing Actions runs",
        "Never force-push",
        "MAXIBOT repository",
        "Read the runbook once per incident",
        "A missing runbook read during healthy access is expected",
    ):
        assert required in text
    assert "before touching GitHub" not in text.lower()


def test_registry_applies_diagnostics_to_actual_github_operators_on_error():
    text = (ROOT / "docs/ROLE_REGISTRY.md").read_text(encoding="utf-8")
    assert "Successful GitHub, GHCR, Git CLI, and GitHub Actions operations do not require" in text
    assert "When an actual DNS, TLS, transport, authentication" in text
    assert "MAXIBOT-specific credentials" in text
    assert "without OWNER authorization" in text
    assert "same unchanged failure" in text
