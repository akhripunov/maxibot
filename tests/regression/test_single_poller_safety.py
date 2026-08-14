from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_application_polling_is_opt_in():
    source = (ROOT / "app/main.py").read_text()
    assert 'os.getenv("MAX_RUN_POLLING", "false")' in source


def test_local_compose_disables_polling():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert 'MAX_RUN_POLLING: "false"' in compose


def test_vps_assigns_polling_only_to_worker():
    compose = (ROOT / "docker-compose.vps.yml").read_text()
    assert 'MAX_RUN_POLLING: "false"' in compose
    assert 'MAX_RUN_POLLING: "true"' in compose
    assert 'command: ["python", "-m", "app.poller"]' in compose
