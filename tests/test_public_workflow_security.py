"""Guard public workflow action pins and executor image publication trust."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def test_prepare_bootstraps_patched_pip_before_test_dependencies():
    workflow = yaml.safe_load((WORKFLOWS / "maxibot-prepare.yml").read_text())
    installs = [
        step["run"] for job in workflow["jobs"].values() for step in job.get("steps", [])
        if step.get("name") == "Install isolated test dependencies"
    ]
    assert len(installs) == 2  # canonical main and isolated branch-check paths
    for script in installs:
        bootstrap = "python3 -m pip install --no-cache-dir pip==26.2.2"
        assert bootstrap in script
        assert script.index(bootstrap) < script.index("python3 -m pip install --disable-pip-version-check -r")


def test_public_workflows_pin_every_action_to_a_full_commit_sha():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text())
        for job_name, job in workflow.get("jobs", {}).items():
            for step in job.get("steps", []):
                action = step.get("uses", "")
                if not action or action.startswith("./"):
                    continue
                reference = action.rsplit("@", 1)[-1]
                assert len(reference) == 40 and all(c in "0123456789abcdef" for c in reference), (
                    f"{path.name}:{job_name} uses mutable/non-SHA action ref {action}"
                )


def test_executor_publication_is_gated_by_read_only_trust_validation():
    workflow = yaml.safe_load((WORKFLOWS / "telegram-executor-ghcr.yml").read_text())
    jobs = workflow["jobs"]
    validate = jobs["validate"]
    publish = jobs["publish"]

    assert workflow["permissions"] == {}
    assert validate["permissions"] == {"contents": "read"}
    assert "github.repository == 'akhripunov/maxibot'" in validate["if"]
    assert "github.actor == 'akhripunov'" in validate["if"]
    assert validate["steps"][0]["with"]["ref"] == "${{ github.sha }}"

    validation_script = validate["steps"][1]["run"]
    assert 'test "$SOURCE_SHA" = "$EVENT_SHA"' in validation_script
    assert 'test "$(git rev-parse HEAD)" = "$SOURCE_SHA"' in validation_script
    assert "git merge-base --is-ancestor" in validation_script
    assert "refs/remotes/origin/main" in validation_script

    assert publish["needs"] == "validate"
    assert "needs.validate.result == 'success'" in publish["if"]
    assert publish["permissions"] == {"contents": "write", "packages": "write"}
    assert publish["env"]["SOURCE_SHA"] == "${{ needs.validate.outputs.source_sha }}"
    assert publish["steps"][0]["with"]["ref"] == "${{ needs.validate.outputs.source_sha }}"
    assert "github.repository == 'akhripunov/maxibot'" in publish["if"]
    assert "github.actor == 'akhripunov'" in publish["if"]


def test_host_ssh_policy_preserves_keys_and_disables_password_login():
    policy = ROOT / "deploy/security/sshd-key-only.conf"
    settings = {}
    for line in policy.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, value = line.split(maxsplit=1)
            assert name.lower() not in settings
            settings[name.lower()] = value
    assert settings == {
        "pubkeyauthentication": "yes",
        "passwordauthentication": "no",
        "kbdinteractiveauthentication": "no",
        "permitrootlogin": "prohibit-password",
        "permitemptypasswords": "no",
        "permituserenvironment": "no",
        "x11forwarding": "no",
    }
