from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_prepare_loads_production_plan_before_frozen_guard():
    workflow = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
    assert workflow.index("Plan MAXIBOT V3 from immutable production evidence") < workflow.index(
        "Validate patch and frozen paid-traffic paths")
    guard_step = workflow[workflow.index("Validate patch and frozen paid-traffic paths"):]
    assert "scripts/prepare_frozen_path_guard.py" in guard_step
    assert "RELEASE_TRIGGER_ACTOR: ${{ github.actor }}" in workflow
    assert "RELEASE_REPOSITORY_OWNER: ${{ github.repository_owner }}" in workflow


def test_prepare_auth_resolver_uses_durable_state_and_owner_identity():
    helper = (ROOT / "scripts/prepare_frozen_path_guard.py").read_text()
    assert "resolve_prepare_authorization" in helper
    assert 'for state in ("revoked", "consumed")' in (ROOT / "scripts/release_authorization.py").read_text()
    assert 'event_name != "workflow_dispatch" or not actor or actor.casefold() != owner.casefold()' in helper
    assert '"*" in p or "?" in p' in helper
