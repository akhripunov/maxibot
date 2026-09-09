from pathlib import Path


def test_staging_e2e_contract_uses_existing_routes():
    script = (Path(__file__).parents[2] / "scripts" / "qa_check.sh").read_text()
    assert "/health/live" in script
    assert "/health/ready" in script
    assert "/mini" in script
    assert "/traffic-test/app" in script
