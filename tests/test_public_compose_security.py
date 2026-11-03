from pathlib import Path
import yaml

ROOT = Path(__file__).parents[1]

def test_python_images_upgrade_pip_before_installing_project():
    for path in ("Dockerfile", "Dockerfile.staging", "Dockerfile.qa.local", "deploy/telegram-executor/Dockerfile"):
        dockerfile = (ROOT / path).read_text()
        bootstrap = "RUN python -m pip install --no-cache-dir pip==26.2.2"
        assert bootstrap in dockerfile, path
        assert dockerfile.index(bootstrap) < dockerfile.index("RUN pip install"), path

def test_proxy_secrets():
    s = yaml.safe_load((ROOT/'docker-compose.vps.yml').read_text())['services']
    assert 'env_file' not in s['https']
    assert set(s['https']['environment']) == {'WEB_ADMIN_INTERNAL_TOKEN'}


def test_networks_and_ports():
    s = yaml.safe_load((ROOT/'docker-compose.vps.yml').read_text())['services']
    assert set(s['https']['networks']).isdisjoint(s['db']['networks'])
    assert set(s['https']['networks']) <= set(s['api']['networks'])
    assert set(s['db']['networks']) <= set(s['api']['networks'])
    assert not s['db'].get('ports') and not s['redis'].get('ports')


def test_database_password_required():
    s = yaml.safe_load((ROOT/'docker-compose.vps.yml').read_text())['services']
    assert ':?' in s['db']['environment']['POSTGRES_PASSWORD']
