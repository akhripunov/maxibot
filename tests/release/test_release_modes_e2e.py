"""Isolated DB-only and app+DB mode checks using disposable PostgreSQL and Docker containers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]
REHEARSAL = ROOT / "scripts/postgres_migration_rehearsal.py"
TARGET = "0099_metrica_upload_reconciliation_v1"
CONTAINER_IMAGE = "postgres:16.6"


def _require_e2e_prerequisites() -> str:
    database_url = os.environ.get("RELEASE_MODE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("requires the isolated PostgreSQL service configured by Native Release CI")
    docker = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if docker.returncode:
        if os.environ.get("CI", "").lower() == "true":
            pytest.fail("Native Release CI must provide a working Docker daemon for mode E2E")
        pytest.skip("local Docker daemon is unavailable")
    image = subprocess.run(
        ["docker", "image", "inspect", CONTAINER_IMAGE], capture_output=True, text=True
    )
    if image.returncode:
        if os.environ.get("CI", "").lower() == "true":
            pytest.fail(f"expected PostgreSQL service image is not available locally: {CONTAINER_IMAGE}")
        pytest.skip(f"Docker image is not cached locally: {CONTAINER_IMAGE}")
    return database_url


def _create_database(admin_url: str, database: str) -> None:
    engine = create_engine(admin_url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        engine.dispose()


def _drop_database(admin_url: str, database: str) -> None:
    engine = create_engine(admin_url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :database AND pid <> pg_backend_pid()"
            ), {"database": database})
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
    finally:
        engine.dispose()


def _container_snapshot(name: str) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "inspect", name], check=True, capture_output=True, text=True, timeout=20
    )
    item = json.loads(result.stdout)[0]
    assert item["State"]["Running"] is True
    return {"id": item["Id"], "started_at": item["State"]["StartedAt"]}


def _start_controlled_container(name: str) -> None:
    subprocess.run(
        ["docker", "run", "--pull=never", "--detach", "--name", name,
         "--network", "none", CONTAINER_IMAGE, "sleep", "infinity"],
        check=True, capture_output=True, text=True, timeout=60,
    )
    _container_snapshot(name)


def _replace_controlled_container(name: str) -> dict[str, str]:
    before = _container_snapshot(name)
    subprocess.run(["docker", "rm", "--force", name], check=True, capture_output=True, text=True, timeout=30)
    _start_controlled_container(name)
    after = _container_snapshot(name)
    assert after["id"] != before["id"]
    assert after["started_at"] != before["started_at"]
    return after


@pytest.mark.parametrize("mode", ["db-only", "app+db"])
def test_isolated_schema_apply_and_application_container_transition(mode: str) -> None:
    database_url = _require_e2e_prerequisites()
    base_url = make_url(database_url)
    admin_url = base_url.set(database="postgres")
    database = f"maxibot_mode_{mode.replace('+', '_')}_{uuid.uuid4().hex}"
    suffix = uuid.uuid4().hex[:12]
    containers = (f"maxibot-mode-api-{suffix}", f"maxibot-mode-worker-{suffix}")
    target_url = base_url.set(database=database)
    created_database = False
    started_containers: list[str] = []

    try:
        _create_database(admin_url.render_as_string(hide_password=False), database)
        created_database = True
        for name in containers:
            started_containers.append(name)
            _start_controlled_container(name)
        initial = {name: _container_snapshot(name) for name in containers}

        environment = os.environ.copy()
        environment["DATABASE_URL"] = target_url.render_as_string(hide_password=False)
        result = subprocess.run(
            [sys.executable, str(REHEARSAL)], cwd=ROOT, env=environment,
            capture_output=True, text=True, timeout=900,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "POSTGRES_MIGRATION_REHEARSAL=PASS" in result.stdout
        engine = create_engine(target_url)
        try:
            with engine.connect() as connection:
                head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        finally:
            engine.dispose()
        assert head == TARGET

        after_schema = {name: _container_snapshot(name) for name in containers}
        if mode == "db-only":
            assert after_schema == initial, "DB-only schema Apply changed an application container"
            return

        # Mirror the workflow contract: application replacement is reached only
        # after the exact schema target has been verified above.
        assert head == TARGET
        for name in containers:
            _replace_controlled_container(name)
        after_deploy = {name: _container_snapshot(name) for name in containers}
        assert all(after_deploy[name] != initial[name] for name in containers)
    finally:
        for name in reversed(started_containers):
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, text=True, timeout=30)
        if created_database:
            _drop_database(admin_url.render_as_string(hide_password=False), database)
