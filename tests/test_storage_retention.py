import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_retention_is_dry_run_and_fail_closed(tmp_path):
    release = tmp_path / "a"; release.mkdir()
    (release / "release-manifest.json").write_text(json.dumps({"release_id": "a", "target_sha": "a"}))
    (release / "rollback-compose.vps.yml").write_text("services: {}\n")
    result = subprocess.run([sys.executable, "scripts/release_retention.py", "--root", str(tmp_path), "--dry-run"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0
    assert "MODE=DRY_RUN" in result.stdout
    assert "RELEASES_DELETE_CANDIDATES=0" in result.stdout


def test_tmp_ttl_skips_lock_and_current_files(tmp_path):
    stale = tmp_path / "stale.log"; stale.write_text("x")
    lock = tmp_path / "active.lock"; lock.write_text("x")
    current = tmp_path / "current-run"; current.write_text("x")
    old = 1
    for path in (stale, lock, current):
        os.utime(path, (old, old))
    result = subprocess.run([sys.executable, "scripts/tmp_ttl_cleanup.py", "--root", str(tmp_path), "--ttl-hours", "1", "--dry-run"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0
    assert "TMP_DELETE_CANDIDATES=1" in result.stdout
    assert str(stale) in result.stdout


def test_storage_snapshot_is_read_only_by_default(tmp_path):
    output = tmp_path / "samples.jsonl"
    result = subprocess.run([sys.executable, "scripts/storage_snapshot.py", "--output", str(output), "--dry-run"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0
    assert "MODE=DRY_RUN" in result.stdout
    assert not output.exists()


def test_admin_storage_route_is_read_only_and_separate_from_ads():
    source = (ROOT / "app/web_admin_readonly.py").read_text()
    assert '@router.get("/storage-health")' in source
    assert "system_storage_diagnostics" in source
    assert '@router.get("/ads-storage")' in source


def test_production_maintenance_is_canonical_and_dry_run_by_default():
    unit = (ROOT / "deploy/maxibot-docker-safe-maintenance.service").read_text()
    compose = (ROOT / "docker-compose.vps.yml").read_text()
    assert "/opt/maxibot/scripts/docker_safe_maintenance_vps.sh --dry-run" in unit
    assert "/opt/maxibot/storage:/var/lib/maxibot/storage:ro" in compose
