import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migration_rehearsal.py"
SHA = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _run(current, target):
    return subprocess.run(
        [sys.executable, str(SCRIPT), SHA, current, target], cwd=ROOT, capture_output=True, text=True
    )


def test_pending_migration_rehearses_without_production_mutation():
    result = _run("0090_self_learning_loop_v2", "0092_split_yandex_oauth_credentials")
    assert result.returncode == 0, result.stderr
    assert "MIGRATION_REHEARSAL=PASS" in result.stdout
    assert "PATH=0090_self_learning_loop_v2->0091_generic_promotion_platform->0092_split_yandex_oauth_credentials" in result.stdout
    assert "ADS_TABLE_COUNT=16" in result.stdout
    assert "BACKFILL=MAX" in result.stdout


def test_same_head_rehearsal_is_allowed():
    result = _run("0092_split_yandex_oauth_credentials", "0092_split_yandex_oauth_credentials")
    assert result.returncode == 0, result.stderr
    assert "PATH=0092_split_yandex_oauth_credentials" in result.stdout


def test_unrelated_production_head_is_fail_closed():
    result = _run("unrelated-production-head", "0092_split_yandex_oauth_credentials")
    assert result.returncode != 0
    assert "MIGRATION_REHEARSAL=BLOCKED" in result.stderr


def test_missing_target_is_blocked():
    result = _run("0090_self_learning_loop_v2", "does-not-exist")
    assert result.returncode != 0
    assert "MIGRATION_REHEARSAL=BLOCKED" in result.stderr
