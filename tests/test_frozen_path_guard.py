from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/frozen_path_guard.sh"
MANIFEST = ROOT / "docs/frozen_paths.yaml"


def _repo(tmp_path: Path, changed_path: str) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    (repo / "docs").mkdir()
    manifest_text = MANIFEST.read_text(encoding="utf-8")
    (repo / "docs/frozen_paths.yaml").write_text(manifest_text, encoding="utf-8")
    contracts = manifest_text.split("regression_contracts:\n", 1)[1]
    for line in contracts.splitlines():
        if not line.startswith("  - "):
            continue
        contract_path = line[4:].strip()
        contract = repo / contract_path
        contract.parent.mkdir(parents=True, exist_ok=True)
        contract.write_text("# required regression contract\n", encoding="utf-8")
    (repo / "baseline.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True, env=env)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    target = repo / changed_path
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "changed\n"
    if changed_path == "app/main.py":
        content = "#fallbackCta Перейти в MAX\n"
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", changed_path], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True, env=env)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, base, head


def _run(repo: Path, base: str, head: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(GUARD), "--repo", str(repo), "--manifest", "docs/frozen_paths.yaml", "--base", base, "--head", head, *extra],
        text=True,
        capture_output=True,
        check=False,
    )


def test_unrelated_change_passes(tmp_path):
    repo, base, head = _repo(tmp_path, "README.md")
    result = _run(repo, base, head)
    assert result.returncode == 0
    assert "BOT_FROZEN_PATH_CHANGED=NO" in result.stdout
    assert "FROZEN_PATH_GUARD=PASS" in result.stdout


def test_ordinary_menu_runtime_and_tariff_changes_pass_by_behavioral_contract(tmp_path):
    for path in ("app/main.py", "app/bot/menu.py", "app/bot/runtime.py", "app/integrations/subscriptions.py"):
        repo, base, head = _repo(tmp_path / path.replace("/", "_"), path)
        result = _run(repo, base, head)
        assert result.returncode == 0, (path, result.stdout, result.stderr)
        assert "FROZEN_PATH_GUARD=PASS" in result.stdout
        assert "FROZEN_PATH_REGRESSION_CONTRACTS=PASS:16" in result.stdout


def test_channel_change_blocks_without_owner_approval(tmp_path):
    repo, base, head = _repo(tmp_path, "app/traffic/deep_link.py")
    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "CHANNEL_FROZEN_PATH_CHANGED=YES" in result.stdout
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout


def test_critical_conversion_implementation_stays_protected(tmp_path):
    repo, base, head = _repo(tmp_path, "app/traffic/system_conversion.py")
    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "BOT_FROZEN_PATH_CHANGED=YES" in result.stdout
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout


def test_missing_critical_regression_contract_fails_closed(tmp_path):
    repo, base, head = _repo(tmp_path, "README.md")
    contract = repo / "tests/test_traffic_deep_link.py"
    contract.unlink()
    subprocess.run(["git", "add", "-u", "tests/test_traffic_deep_link.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "remove contract"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "MISSING_REGRESSION_CONTRACTS=tests/test_traffic_deep_link.py" in result.stderr
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout


def test_shared_dependency_is_possible_and_blocks_pending_review(tmp_path):
    repo, base, head = _repo(tmp_path, "app/db/models.py")
    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "BOT_FROZEN_PATH_CHANGED=POSSIBLE" in result.stdout
    assert "CHANNEL_FROZEN_PATH_CHANGED=POSSIBLE" in result.stdout
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout


def test_task_scoped_owner_approval_allows_exact_path(tmp_path):
    repo, base, head = _repo(tmp_path, "app/traffic/deep_link.py")
    result = _run(repo, base, head, "--owner-approved-task", "OWNER-123", "--owner-approved-path", "app/traffic/deep_link.py")
    assert result.returncode == 0
    assert "OWNER_FROZEN_CHANGE_APPROVAL=YES" in result.stdout
    assert "FROZEN_PATH_GUARD=PASS" in result.stdout


def test_task_scoped_owner_approval_rejects_wildcard_path(tmp_path):
    repo, base, head = _repo(tmp_path, "app/traffic/deep_link.py")
    result = _run(repo, base, head, "--owner-approved-task", "OWNER-123", "--owner-approved-path", "app/*")
    assert result.returncode == 1
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout
