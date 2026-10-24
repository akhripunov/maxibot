from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/frozen_path_guard.sh"
MANIFEST = ROOT / "docs/frozen_paths.yaml"


def _repo(tmp_path: Path, changed_path: str) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
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
    (repo / "docs/frozen_paths.yaml").write_text(MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
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


def test_bot_change_blocks_without_owner_approval(tmp_path):
    repo, base, head = _repo(tmp_path, "app/main.py")
    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "BOT_FROZEN_PATH_CHANGED=YES" in result.stdout
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout


def test_channel_change_blocks_without_owner_approval(tmp_path):
    repo, base, head = _repo(tmp_path, "app/traffic/deep_link.py")
    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "CHANNEL_FROZEN_PATH_CHANGED=YES" in result.stdout
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout


def test_shared_dependency_is_possible_and_blocks_pending_review(tmp_path):
    repo, base, head = _repo(tmp_path, "app/db/models.py")
    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "BOT_FROZEN_PATH_CHANGED=POSSIBLE" in result.stdout
    assert "CHANNEL_FROZEN_PATH_CHANGED=POSSIBLE" in result.stdout
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout


def test_task_scoped_owner_approval_allows_exact_path(tmp_path):
    repo, base, head = _repo(tmp_path, "app/main.py")
    result = _run(repo, base, head, "--owner-approved-task", "OWNER-123", "--owner-approved-path", "app/main.py")
    assert result.returncode == 0
    assert "OWNER_FROZEN_CHANGE_APPROVAL=YES" in result.stdout
    assert "FROZEN_PATH_GUARD=PASS" in result.stdout


def test_task_scoped_owner_approval_rejects_wildcard_path(tmp_path):
    repo, base, head = _repo(tmp_path, "app/main.py")
    result = _run(repo, base, head, "--owner-approved-task", "OWNER-123", "--owner-approved-path", "app/*")
    assert result.returncode == 1
    assert "FROZEN_PATH_GUARD=BLOCK" in result.stdout
