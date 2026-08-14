from pathlib import Path


def test_staging_has_separate_service_ports_and_staging_env():
    compose = (Path(__file__).parents[2] / "docker-compose.qa.yml").read_text()
    assert "QA_STAGING_PORT:-18000" in compose
    assert "QA_STAGING_DB_PORT:-55432" in compose
    assert "QA_STAGING_REDIS_PORT:-56379" in compose
    assert ".env.staging" in compose
