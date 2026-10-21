from pathlib import Path
import yaml

ROOT = Path(__file__).parents[1]

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


def test_release_compose_env_and_proxy_recreation():
    s = (ROOT/'scripts/release_prod.sh').read_text()
    assert 'docker compose -p' not in s
    assert s.count('up -d --no-build --force-recreate api worker https') == 2


def test_crypto_gate_before_migration():
    s = (ROOT/'scripts/release_prod.sh').read_text()
    assert s.index('timed_stage SECURITY_CONFIG') < s.index('timed_stage MIGRATION')
    assert 'validate_crypto_settings();' in s
