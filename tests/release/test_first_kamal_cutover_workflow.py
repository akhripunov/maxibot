from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/maxibot-first-kamal-cutover.yml"


def test_first_cutover_workflow_is_manual_owner_gated_and_has_separate_apply_confirmation():
    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)

    assert list(workflow["on"]) == ["workflow_dispatch"]
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    for name in ("candidate", "build_run_id", "image_digest", "credential_rotation_attestation", "confirm_apply"):
        assert inputs[name]["required"] == "true"
    assert list(workflow["jobs"]) == ["preflight", "apply"]

    for job_name in ("preflight", "apply"):
        job = workflow["jobs"][job_name]
        assert job["environment"]["name"] == "production"
        assert "github.actor == github.repository_owner" in job["if"]
    assert workflow["jobs"]["apply"]["needs"] == "preflight"
    assert "inputs.confirm_apply == 'APPLY_FIRST_COMPOSE_TO_KAMAL'" in workflow["jobs"]["apply"]["if"]


def test_first_cutover_preflight_checks_immutable_build_and_fails_closed_before_apply():
    workflow = WORKFLOW_PATH.read_text()
    preflight = workflow.split("\n  preflight:\n", 1)[1].split("\n  apply:\n", 1)[0]

    assert "actions/runs/$BUILD_RUN_ID" in preflight
    assert '.head_sha == $sha' in preflight
    assert '.conclusion == "success"' in preflight
    assert 'docker buildx imagetools inspect "$image_tag"' in preflight
    assert 'test "$readback_digest" = "$APPROVED_DIGEST"' in preflight
    assert 'docker pull "$image_ref"' in preflight
    assert "linux/amd64" in preflight
    assert "SBOM" in preflight and "provenance" in preflight.lower()
    assert "CREDENTIAL_ROTATION_ATTESTATION" in preflight and "== ROTATED" in preflight
    assert '.name == "tests" and .conclusion == "success"' in preflight
    assert '.name == "build" and .conclusion == "success"' in preflight
    assert "StrictHostKeyChecking=yes" in preflight
    assert "StrictHostKeyChecking yes" in preflight
    assert 'install -m 600 "$RUNNER_TEMP/ssh/id_ed25519" "$HOME/.ssh/id_ed25519"' in preflight
    assert 'UserKnownHostsFile $HOME/.ssh/known_hosts' in preflight
    assert "MAXIBOT_PRODUCTION_SSH_KEY" in preflight
    assert "MAXIBOT_PRODUCTION_KNOWN_HOSTS" in preflight
    assert "repos/$GITHUB_REPOSITORY/environments/production" in preflight
    assert ".prevent_self_review == false" in preflight
    assert "required_reviewers" in preflight
    assert "https://api.maxibot.pro/health/ready" in preflight
    assert "public_ready" in preflight
    assert "pg_restore --list" in preflight
    assert "cutover_restore_probe" in preflight
    assert "docker build-push-action@" not in workflow
    assert "docker compose up" not in workflow
    assert "kamal deploy" not in preflight


def test_first_cutover_apply_preserves_two_networks_single_poller_and_has_recovery_gates():
    workflow = WORKFLOW_PATH.read_text()
    apply = workflow.split("\n  apply:\n", 1)[1]

    assert "maxibot-vps-internal" in apply
    assert "maxibot-vps-frontend" in apply
    assert "kamal" in apply
    assert "MAX_RUN_POLLING=true" in apply
    assert "MAX_RUN_POLLING=false" in apply
    assert "536870912" in apply
    assert "bundle exec kamal deploy --skip-push --version \"$IMAGE_VERSION\" --roles api" in apply
    assert "bundle exec kamal deploy --skip-push --version \"$IMAGE_VERSION\" --roles worker" in apply
    assert "do not auto-rollback" in apply
    assert "bundle exec kamal app stop --roles worker" in apply
    assert "bundle exec kamal app stop --roles api" in apply
    assert "docker compose" in apply
    assert "IMAGE_DIGEST" in apply and "RepoDigests" in apply
    assert "EXPECTED_DATABASE_HEAD" in apply
    assert "EXPECTED_CADDY_ID" in apply
    assert "StrictHostKeyChecking=yes" in apply


def test_preflight_is_read_only_and_only_the_explicit_apply_job_changes_services():
    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)
    preflight = "\n".join(step.get("run", "") for step in workflow["jobs"]["preflight"]["steps"])
    apply = "\n".join(step.get("run", "") for step in workflow["jobs"]["apply"]["steps"])

    assert list(workflow["on"]) == ["workflow_dispatch"]
    assert "--network none" in preflight
    assert "trap cleanup EXIT" in preflight
    assert "docker network connect" not in preflight
    assert "docker stop maxibot-vps-api-1" not in preflight
    assert "docker stop maxibot-vps-worker-1" not in preflight
    assert "bundle exec kamal deploy" not in preflight
    assert "docker network connect --alias" in apply
    assert "docker stop maxibot-vps-worker-1" in apply
    assert "docker stop maxibot-vps-api-1" in apply
    assert "inputs.confirm_apply == 'APPLY_FIRST_COMPOSE_TO_KAMAL'" in workflow["jobs"]["apply"]["if"]


def test_environment_readback_uses_a_supported_github_token_permission():
    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)

    for job_name in ("preflight", "apply"):
        permissions = workflow["jobs"][job_name]["permissions"]
        assert permissions["actions"] == "read"
        assert "administration" not in permissions
        commands = "\n".join(step.get("run", "") for step in workflow["jobs"][job_name]["steps"])
        assert "repos/$GITHUB_REPOSITORY/environments/production" in commands
        assert ".prevent_self_review == false" in commands
        assert "required_reviewers" in commands


def test_existing_build_sha_must_be_exact_and_ancestor_of_dispatch_main():
    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)
    preflight = "\n".join(step.get("run", "") for step in workflow["jobs"]["preflight"]["steps"])
    apply = "\n".join(step.get("run", "") for step in workflow["jobs"]["apply"]["steps"])

    for commands in (preflight, apply):
        assert '.head_sha == $sha' in commands or '.head_sha==$sha' in commands
        assert 'and .head_branch == "main"' in commands or '.head_branch=="main"' in commands
        assert 'and .event == "push"' in commands or '.event=="push"' in commands
        assert 'compare/$CANDIDATE_SHA...$main_sha' in commands
        assert 'test "$main_sha" = "$GITHUB_SHA"' in commands
        assert '.status == "ahead" or .status == "identical"' in commands or '.status=="ahead" or .status=="identical"' in commands
    assert 'test "$main_sha" = "$CANDIDATE_SHA"' not in preflight
    assert 'test "$main_sha" = "$CANDIDATE_SHA"' not in apply
