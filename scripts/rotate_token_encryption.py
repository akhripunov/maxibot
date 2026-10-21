"""Offline inspection and transactional token re-encryption building blocks.

CLI deliberately exposes no apply mode. Production callers must hold the release
lock and stop writers; this module never retrieves or prints production secrets.
"""
import argparse
import base64
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import inspect, text

ENCRYPTED_COLUMNS = {
    'traffic_profiles': ('yandex_oauth_token_encrypted', 'yandex_metrica_oauth_token_encrypted', 'yandex_direct_oauth_token_encrypted'),
    'system_promotion_profiles': ('yandex_oauth_token_encrypted',),
    'bot_promotion_profiles': ('yandex_oauth_token_encrypted',),
    'bot_connections': ('encrypted_token',),
    'telegram_ads_service_identities': ('encrypted_session',),
}
ALLOWED = {f'{table}.{column}' for table, columns in ENCRYPTED_COLUMNS.items() for column in columns}

class RotationError(Exception):
    """Messages contain no credential, ciphertext, or record identifier."""


def derive_app_secret_fernet_key(secret):
    if not secret:
        raise RotationError('Missing legacy secret')
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _keys(old_key, new_key):
    try:
        old = old_key.encode() if isinstance(old_key, str) else old_key
        new = new_key.encode() if isinstance(new_key, str) else new_key
        a, b = Fernet(old), Fernet(new)
        if base64.urlsafe_b64decode(old) == base64.urlsafe_b64decode(new):
            raise RotationError('Distinct keys required')
        return a, b
    except (ValueError, TypeError):
        raise RotationError('Invalid encryption key') from None


def rotate_ciphertext(value, old_key, new_key):
    old, new = _keys(old_key, new_key)
    try:
        raw = value.encode() if isinstance(value, str) else value
        try:
            plain = old.decrypt(raw)
        except InvalidToken:
            new.decrypt(raw)
            return value, False
        result = new.encrypt(plain)
        if new.decrypt(result) != plain:
            raise RotationError('Encryption verification failed')
        return (result.decode() if isinstance(value, str) else result), True
    except (InvalidToken, TypeError, AttributeError):
        raise RotationError('Unreadable encrypted value') from None


def rotate_values(values, old_key, new_key):
    _keys(old_key, new_key)
    if set(values) - ALLOWED:
        raise RotationError('Unknown encrypted column')
    result, count = {}, 0
    for column, value in values.items():
        if value is None or value == '':
            result[column] = value
        else:
            result[column], changed = rotate_ciphertext(value, old_key, new_key)
            count += int(changed)
    return result, count


def _schema(connection):
    schema = inspect(connection)
    names = schema.get_table_names()
    for table in names:
        for col in schema.get_columns(table):
            name = col['name']
            if ('encrypted' in name.lower() or 'cipher' in name.lower()) and f'{table}.{name}' not in ALLOWED:
                raise RotationError('Unknown encrypted schema')
    for table, columns in ENCRYPTED_COLUMNS.items():
        if table not in names:
            raise RotationError('Missing encrypted schema')
        actual = {c['name'] for c in schema.get_columns(table)}
        if not {'id', *columns} <= actual:
            raise RotationError('Missing encrypted schema')


def rotate_in_transaction(connection, old_key, new_key):
    if not connection.in_transaction():
        raise RotationError('Explicit transaction required')
    _keys(old_key, new_key)
    _schema(connection)
    count = 0
    for table, columns in ENCRYPTED_COLUMNS.items():
        for row in connection.execute(text(f'SELECT id, {", ".join(columns)} FROM {table}')).mappings():
            values = {f'{table}.{c}': row[c] for c in columns}
            rotated, changed = rotate_values(values, old_key, new_key)
            if changed:
                params = {c: rotated[f'{table}.{c}'] for c in columns}
                params['_row_id'] = row['id']
                assignments = ', '.join(f'{c} = :{c}' for c in columns)
                connection.execute(text(f'UPDATE {table} SET {assignments} WHERE id = :_row_id'), params)
                count += changed
    return {'rotated_values': count}


def rotate_database(engine, old_key, new_key):
    with engine.begin() as connection:
        return rotate_in_transaction(connection, old_key, new_key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['inspect', 'dry-run'], required=True)
    parser.add_argument('--database', required=True, help='Offline SQLite snapshot')
    old_group = parser.add_mutually_exclusive_group(required=True)
    old_group.add_argument('--old-app-secret-env')
    old_group.add_argument('--old-key-env', help='Explicit existing Fernet key environment variable')
    parser.add_argument('--new-key-env')
    args = parser.parse_args()
    try:
        key = (os.environ.get(args.old_key_env, '').encode() if args.old_key_env
               else derive_app_secret_fernet_key(os.environ.get(args.old_app_secret_env, '')))
        old = Fernet(key)
        new = os.environ.get(args.new_key_env, '') if args.new_key_env else None
        if args.mode == 'dry-run' and not new:
            raise RotationError('New key required')
        if new:
            _keys(key, new)
        uri = Path(args.database).resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                quoted = table.replace(chr(34), chr(34) * 2)
                columns = {row[1] for row in db.execute(f'PRAGMA table_info("{quoted}")')}
                if any(('encrypted' in col.lower() or 'cipher' in col.lower()) and f'{table}.{col}' not in ALLOWED for col in columns):
                    raise RotationError('Unknown encrypted schema')
                if table in ENCRYPTED_COLUMNS and not set(ENCRYPTED_COLUMNS[table]) <= columns:
                    raise RotationError('Missing encrypted schema')
            if not set(ENCRYPTED_COLUMNS) <= set(tables):
                raise RotationError('Missing encrypted schema')
            result = {}
            for table, columns in ENCRYPTED_COLUMNS.items():
                for column in columns:
                    count, valid, changed = 0, True, 0
                    for (value,) in db.execute(f'SELECT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != \'\''):
                        count += 1
                        try:
                            if new:
                                _, did_change = rotate_ciphertext(value, key, new)
                                changed += int(did_change)
                            else:
                                old.decrypt(value.encode())
                        except (InvalidToken, RotationError):
                            valid = False
                    result[f'{table}.{column}'] = {'count': count, 'all_decryptable': valid, 'would_rotate': changed}
            print(json.dumps(result, sort_keys=True))
            return 0 if all(r['all_decryptable'] for r in result.values()) else 2
    except Exception:
        print(json.dumps({'error': 'Offline inspection failed; no changes applied'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
