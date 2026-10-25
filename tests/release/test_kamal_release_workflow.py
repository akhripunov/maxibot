from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/maxibot-native-release.yml"


def test_kamal_release_has_three_jobs_and_owner_gated_deploy():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)

    assert "main" in workflow["on"]["push"]["branches"]
    assert workflow["on"]["pull_request"]["branches"] == ["main"]
    assert workflow["on"]["workflow_dispatch"]["inputs"]["candidate"]["required"] == "true"
    assert workflow["on"]["workflow_dispatch"]["inputs"]["build_run_id"]["required"] == "true"
    assert workflow["on"]["workflow_dispatch"]["inputs"]["image_digest"]["required"] == "true"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert list(workflow["jobs"]) == ["tests", "build", "deploy"]
    assert workflow["jobs"]["build"]["needs"] == "tests"
    assert "workflow_dispatch" not in workflow["jobs"]["build"]["if"]
    assert "github.event_name == 'push'" in workflow["jobs"]["build"]["if"]
    assert workflow["jobs"]["deploy"].get("needs") is None
    assert "inputs.mode" not in workflow["jobs"]["build"]["if"]
    assert workflow["jobs"]["deploy"]["environment"]["name"] == "production"
    assert workflow["jobs"]["deploy"]["permissions"]["packages"] == "read"
    assert workflow["jobs"]["deploy"]["permissions"]["actions"] == "read"


def test_kamal_build_is_run_scoped_and_records_source_sha_and_digest():
    workflow = WORKFLOW.read_text()

    assert "value=$CANDIDATE_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" in workflow
    assert "tags: ghcr.io/akhripunov/maxibot-vps-api:${{ steps.version.outputs.value }}" in workflow
    assert "org.opencontainers.image.revision=${{ env.CANDIDATE_SHA }}" in workflow
    assert "steps.image.outputs.digest" in workflow
    assert 'MAXIBOT_BUILD_RUN_ID=$GITHUB_RUN_ID' in workflow
    assert 'MAXIBOT_BUILD_RUN_ATTEMPT=$GITHUB_RUN_ATTEMPT' in workflow
    assert "actions/upload-artifact@" not in workflow
    assert "actions/download-artifact@" not in workflow
    assert "packages: write" in workflow


def test_kamal_deploy_selects_a_prior_successful_build_and_verifies_its_digest():
    workflow = WORKFLOW.read_text()
    deploy = workflow.split("\n  deploy:\n", 1)[1]

    assert 'actions/runs/$BUILD_RUN_ID' in deploy
    assert '.head_sha == $sha' in deploy
    assert '.head_branch == "main"' in deploy
    assert '.event == "push"' in deploy
    assert '.status == "completed"' in deploy
    assert '.conclusion == "success"' in deploy
    assert '.path == ".github/workflows/maxibot-native-release.yml@refs/heads/main"' in deploy
    assert 'image_version="$CANDIDATE_SHA-$BUILD_RUN_ID-$build_attempt"' in deploy
    assert 'APPROVED_DIGEST' in deploy
    assert 'test "$image_digest" = "$APPROVED_DIGEST"' in deploy
    assert 'git/ref/heads/main' in deploy
    assert 'docker buildx imagetools inspect "$IMAGE_TAG"' in deploy
    assert 'docker pull "$IMAGE_REF"' in deploy
    assert 'needs.build.outputs' not in deploy
    assert "docker/build-push-action@" not in deploy
    assert deploy.index("Select and verify the prior successful exact-SHA build image") < deploy.index("actions/checkout@")
    assert 'bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles api' in deploy
    assert 'bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles worker' in deploy
    assert 'bundle exec kamal app stop --roles worker' in deploy
    assert 'bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION"' in deploy
    assert "TASK_SCOPED_AUTO" not in deploy
    assert "claimed" not in deploy
    assert "consumed" not in deploy


def test_kamal_deploy_verifies_only_api_worker_and_preserves_safety_invariants():
    workflow = WORKFLOW.read_text()
    deploy = workflow.split("\n  deploy:\n", 1)[1].split("\n  rollback:\n", 1)[0]

    assert "--roles api" in deploy and "--roles worker" in deploy
    assert "worker_health_preflight.py" in deploy
    assert "MAX_RUN_POLLING=true" in deploy
    assert "536870912" in deploy
    assert "health/ready" in deploy
    assert "docker compose" not in deploy
    assert '"pg_isready","-U","maxibot","-d","maxibot"' in deploy
    assert '"redis-cli","ping"' in deploy
    assert '"caddy","validate","--config","/etc/caddy/Caddyfile"' in deploy
    assert 'assert not old["State"]["Running"]' in deploy
    assert "kamal rollback" not in deploy


def test_kamal_config_uses_existing_networks_and_caddy_alias():
    config = yaml.safe_load((ROOT / "config/deploy.yml").read_text())
    compose = (ROOT / "docker-compose.vps.yml").read_text()

    assert config["servers"]["api"]["options"]["network"] == "maxibot-vps-frontend"
    assert config["servers"]["api"]["options"]["network-alias"] == "api"
    assert "network" not in config["servers"]["worker"]["options"]
    assert "name: maxibot-vps-internal" in compose
    assert "name: maxibot-vps-frontend" in compose
    assert "accessories" not in config
    assert config["servers"]["api"]["options"]["volume"] == "maxibot-vps_maxibot_uploads:/app/uploads"
    assert config["servers"]["worker"]["options"]["memory"] == "512m"
