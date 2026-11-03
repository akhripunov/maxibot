"""V3 fail-closed selection, immutable evidence, and exact diff verification."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("maxibot_impact_v3", ROOT / "scripts/maxibot_impact_v3.py")
v3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v3)


def git(repo, *argv):
    return subprocess.check_output(["git", *argv], cwd=repo, text=True).strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "v3-test@example.test")
    git(tmp_path, "config", "user.name", "V3")
    (tmp_path / "miniapp").mkdir()
    (tmp_path / "miniapp" / "index.html").write_text("original")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "production")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


def commit(repo, filename, content):
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "change")
    return git(repo, "rev-parse", "HEAD")


def test_ui_only_proof_exact_full_diff(repository):
    repo, base = repository
    sha = commit(repo, "miniapp/index.html", "new title")
    paths = v3.changed_paths(repo, base, sha)
    evidence = v3.plan(sha, base, paths, "777", "1", reason="immutable-production-verified")
    assert evidence["classification"] == "UI_ONLY"
    assert evidence["test_scope"] == "UI_ONLY"
    assert v3.verify_plan(evidence, repo, sha) == "UI_ONLY"
    assert "ads_write_flags_off" in evidence["required_invariants"]


@pytest.mark.parametrize("path", [
    "miniapp/app.js", "miniapp/traffic.html", "app/core/config.py",
    "app/bot/runtime.py", "alembic/versions/migration.py",
    "docs/frozen_paths.yaml", ".github/workflows/maxibot-prepare.yml",
    "Dockerfile", "pyproject.toml", "tests/test_owner_miniapp.py",
    "app/static/admin/admin.js",
])
def test_unknown_and_paid_paths_always_full(path):
    sha, base = "a" * 40, "b" * 40
    result = v3.plan(sha, base, [path], "777", "1", reason="immutable-production-verified")
    assert result["classification"] == "FULL"


def test_unreleased_backend_commit_before_ui_change_requires_full(repository):
    repo, base = repository
    commit(repo, "app/main.py", "changed backend")
    sha = commit(repo, "miniapp/index.html", "new title")
    result = v3.plan(sha, base, v3.changed_paths(repo, base, sha), "77", "1",
                     reason="immutable-production-verified")
    assert result["classification"] == "FULL"


def test_tampered_scope_and_candidate_are_rejected(repository):
    repo, base = repository
    sha = commit(repo, "miniapp/index.html", "new")
    evidence = v3.plan(sha, base, ["miniapp/index.html"], "7", "1",
                       reason="immutable-production-verified")
    for changed in (
        {"candidate_sha": "c" * 40},
        {"production_sha": "d" * 40},
        {"changed_paths": ["miniapp/style.css"]},
        {"test_scope": "FULL"},
        {"required_invariants": ["exact_sha_build"]},
        {"production_run_id": "no"},
    ):
        with pytest.raises(ValueError):
            v3.verify_plan(dict(evidence, **changed), repo, sha)


def test_missing_immutable_evidence_fail_closed(repository):
    repo, base = repository
    sha = commit(repo, "miniapp/index.html", "new")
    assert v3.build_plan(repo, sha, "refs/heads/feature", "token")["classification"] == "FULL"
    assert v3.build_plan(repo, sha, "refs/heads/main", None)["classification"] == "FULL"
    (repo / "ignored").write_text("untracked")
    with patch.object(v3, "latest_verified_production", return_value=(base, "1", "1")):
        assert v3.build_plan(repo, sha, "refs/heads/main", "token")["classification"] == "FULL"


def test_immutable_source_happy_path(repository):
    repo, base = repository
    sha = commit(repo, "miniapp/owner.css", "white")
    with patch.object(v3, "latest_verified_production", return_value=(base, "1", "1")):
        evidence = v3.build_plan(repo, sha, "refs/heads/main", "token")
    assert evidence["classification"] == "UI_ONLY"
    assert v3.verify_plan(evidence, repo, sha) == "UI_ONLY"


def test_renames_detect_deleted_old_protected_path(repository):
    repo, base = repository
    commit(repo, "app/core/config.py", "original")
    prod = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "app/core/config.py", "miniapp/owner.css")
    git(repo, "commit", "-qm", "renamed")
    sha = git(repo, "rev-parse", "HEAD")
    paths = v3.changed_paths(repo, prod, sha)
    assert "app/core/config.py" in paths
    assert v3.plan(sha, prod, paths, "1", "1", reason="immutable-production-verified")["classification"] == "FULL"


def test_latest_production_require_verified_success_and_immutable_tag():
    import types
    sha = "f" * 40
    run = {"id": 77, "run_attempt": 1, "head_sha": sha,
           "name": "MAXIBOT production " + sha + " by owner",
           "status": "completed", "conclusion": "success", "created_at": "2026-10-01"}
    result = {"status": "COMPLETE", "mode": "production", "candidate_sha": sha,
              "final_runtime_sha": sha, "github_run_id": "77", "github_run_attempt": "1"}
    pub = types.ModuleType("publish_release_to_linear")
    pub.latest_runs = lambda _: [run]
    pub.github_json = lambda _: {"immutable": True, "draft": False}
    pub.read_result_artifact = lambda _: result
    assets = types.ModuleType("release_assets")
    assets.release_tag = lambda *args: "verified-tag"
    with patch.dict(sys.modules, {"publish_release_to_linear": pub, "release_assets": assets}):
        assert v3.latest_verified_production() == (sha, "77", "1")
        pub.latest_runs = lambda _: [{**run, "status": "in_progress"}]
        with pytest.raises(ValueError, match="Unresolved"):
            v3.latest_verified_production()
        pub.latest_runs = lambda _: [run]
        pub.github_json = lambda _: {"immutable": False, "draft": False}
        with pytest.raises(ValueError, match="immutable"):
            v3.latest_verified_production()


def test_full_scope_does_not_claim_partial_test_coverage():
    sha = "a" * 40
    evidence = v3.plan(sha)
    assert v3.verify_plan(evidence, ROOT, sha) == "FULL"
    evidence["test_scope"] = "UI_ONLY"
    with pytest.raises(ValueError, match="FULL"):
        v3.verify_plan(evidence, ROOT, sha)
