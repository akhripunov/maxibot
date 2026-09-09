from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_api_and_worker_load_main_then_protected_mail_env():
    compose = (ROOT / "docker-compose.vps.yml").read_text()
    expected = "      - .env\n      - /opt/maxibot/secrets/mail_cloud.env\n"

    def service_block(name, next_name):
        return compose.split(f"  {name}:\n", 1)[1].split(f"  {next_name}:\n", 1)[0]

    api = service_block("api", "db")
    worker = service_block("worker", "https")
    assert expected in api
    assert expected in worker
    assert compose.count("/opt/maxibot/secrets/mail_cloud.env") == 2
    assert "MAIL_CLOUD_APP_PASSWORD" not in compose


def test_release_rewrites_main_env_but_preserves_protected_secret_path():
    release = (ROOT / "scripts/release_prod.sh").read_text()
    compose = (ROOT / "docker-compose.vps.yml").read_text()
    assert 's.replace("      - .env\\n", "      - /opt/maxibot/.env\\n")' in release
    assert "/opt/maxibot/secrets/mail_cloud.env" in compose
    assert "MAIL_CLOUD_APP_PASSWORD=" not in release
