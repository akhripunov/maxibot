from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_staging_compose_does_not_use_production_env_or_volume():
    compose = (ROOT / "docker-compose.qa.yml").read_text()
    assert "env_file: .env.staging" in compose
    assert "env_file: .env\n" not in compose
    assert "maxibot_staging_postgres_data" in compose


def test_landing_contract_has_no_required_continue_button():
    source = (ROOT / "app" / "main.py").read_text()
    assert "location.replace(target)" in source
    assert 'id="continue"' not in source
