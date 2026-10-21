import importlib.util
from pathlib import Path
import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, text

spec = importlib.util.spec_from_file_location('rotation', Path(__file__).parents[1] / 'scripts/rotate_token_encryption.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

@pytest.fixture
def keys():
    return Fernet.generate_key(), Fernet.generate_key()

@pytest.fixture
def database(keys):
    engine = create_engine('sqlite://')
    old, _ = keys
    with engine.begin() as c:
        for table, columns in r.ENCRYPTED_COLUMNS.items():
            definitions = ', '.join(f'{col} TEXT' for col in columns)
            c.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, {definitions})'))
            c.execute(text(f'INSERT INTO {table} (id, {columns[0]}) VALUES (1, :value)'), {'value': Fernet(old).encrypt(b'fixture-only').decode()})
    return engine


def test_round_trip_idempotent(keys):
    old, new = keys
    value = Fernet(old).encrypt(b'fixture-only').decode()
    rotated, changed = r.rotate_ciphertext(value, old, new)
    assert changed and Fernet(new).decrypt(rotated.encode()) == b'fixture-only'
    with pytest.raises(InvalidToken): Fernet(old).decrypt(rotated.encode())
    assert r.rotate_ciphertext(rotated, old, new) == (rotated, False)


def test_unknown_column_and_null(keys):
    assert r.rotate_values({'bot_connections.encrypted_token': None}, *keys) == ({'bot_connections.encrypted_token': None}, 0)
    with pytest.raises(r.RotationError): r.rotate_values({'unknown.secret': None}, *keys)


def test_invalid_keys_and_cipher(keys):
    with pytest.raises(r.RotationError): r.rotate_ciphertext('bad', *keys)
    with pytest.raises(r.RotationError): r.rotate_ciphertext('bad', b'invalid', keys[1])
    with pytest.raises(r.RotationError): r.rotate_ciphertext('bad', keys[0], keys[0].decode())


def test_legacy_key():
    import base64, hashlib
    assert r.derive_app_secret_fernet_key('fixture-only') == base64.urlsafe_b64encode(hashlib.sha256(b'fixture-only').digest())


def test_database_idempotent(database, keys):
    assert r.rotate_database(database, *keys)['rotated_values'] == 5
    assert r.rotate_database(database, *keys)['rotated_values'] == 0


def test_rollback_on_late_corruption(database, keys):
    with database.begin() as c:
        original = c.execute(text('SELECT yandex_oauth_token_encrypted FROM traffic_profiles')).scalar_one()
        c.execute(text("UPDATE telegram_ads_service_identities SET encrypted_session = 'corrupt-fixture'"))
    with pytest.raises(r.RotationError): r.rotate_database(database, *keys)
    with database.connect() as c:
        assert c.execute(text('SELECT yandex_oauth_token_encrypted FROM traffic_profiles')).scalar_one() == original


def test_explicit_transaction_required(database, keys):
    with database.connect() as c:
        with pytest.raises(r.RotationError, match='Explicit'): r.rotate_in_transaction(c, *keys)


def test_unknown_schema(database, keys):
    with database.begin() as c: c.execute(text('CREATE TABLE unexpected (id INTEGER, encrypted_value TEXT)'))
    with pytest.raises(r.RotationError, match='Unknown'): r.rotate_database(database, *keys)


def test_cli_readonly(tmp_path, monkeypatch, capsys):
    import sqlite3, sys
    path = tmp_path / 'snapshot.sqlite'
    secret = 'fixture-offline-secret'
    cipher = Fernet(r.derive_app_secret_fernet_key(secret)).encrypt(b'fixture-only').decode()
    with sqlite3.connect(path) as c:
        for table, columns in r.ENCRYPTED_COLUMNS.items():
            c.execute(f'CREATE TABLE {table} (id INTEGER, '+', '.join(f'{col} TEXT' for col in columns)+')')
            c.execute(f'INSERT INTO {table} (id,{columns[0]}) VALUES (?,?)', (777, cipher))
    before = path.read_bytes()
    monkeypatch.setenv('ROTATION_FIXTURE_SECRET', secret)
    monkeypatch.setenv('ROTATION_FIXTURE_KEY', Fernet.generate_key().decode())
    monkeypatch.setattr(sys, 'argv', ['rotation', '--mode', 'dry-run', '--database', str(path), '--old-app-secret-env', 'ROTATION_FIXTURE_SECRET', '--new-key-env', 'ROTATION_FIXTURE_KEY'])
    assert r.main() == 0
    output = capsys.readouterr().out
    assert cipher not in output and secret not in output and '777' not in output
    assert before == path.read_bytes()
