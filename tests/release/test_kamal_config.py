from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/maxibot-native-release.yml"


def test_kamal_is_pinned_and_has_a_production_configuration():
    gemfile = (ROOT / "Gemfile.kamal").read_text()
    config = yaml.safe_load((ROOT / "config/deploy.yml").read_text())

    assert 'gem "kamal", "2.12.0"' in gemfile
    assert config["service"] == "maxibot"
    assert config["image"] == "akhripunov/maxibot-vps-api"
    assert config["registry"]["server"] == "ghcr.io"
    assert config["ssh"]["user"] == "root"
    assert all(role["proxy"] is False for role in config["servers"].values())


def test_kamal_owns_only_api_and_single_polling_worker():
    config = yaml.safe_load((ROOT / "config/deploy.yml").read_text())
    roles = config["servers"]

    assert set(roles) == {"api", "worker"}
    assert roles["api"]["cmd"] == "uvicorn app.main:app --host 0.0.0.0 --port 8000"
    assert roles["api"]["env"]["clear"]["MAX_RUN_POLLING"] is False
    assert roles["api"]["env"]["clear"]["ADS_AUTOPILOT_ENABLED"] is False
    assert roles["api"]["env"]["clear"]["ADS_WRITE_ACTIONS_ENABLED"] is False
    assert roles["worker"]["cmd"] == "python -m app.poller"
    assert roles["worker"]["env"]["clear"]["MAX_RUN_POLLING"] is True
    assert roles["worker"]["env"]["clear"]["ADS_AUTOPILOT_ENABLED"] is False
    assert roles["worker"]["env"]["clear"]["ADS_WRITE_ACTIONS_ENABLED"] is False
    assert roles["worker"]["options"]["memory"] == "512m"
    assert roles["worker"]["options"]["memory-swap"] == "512m"
    assert roles["api"]["options"]["network"] == "maxibot-vps-frontend"
    assert roles["api"]["options"]["network-alias"] == "api"
    assert "network" not in roles["worker"]["options"]
    assert "db" not in roles and "redis" not in roles and "https" not in roles
    assert "accessories" not in config
    for role in roles.values():
        release_env = role["env"]["clear"]
        assert release_env["MAXIBOT_RELEASE_ID"] == '<%= ENV.fetch("MAXIBOT_COMMIT_SHA") %>'
        assert release_env["MAXIBOT_RELEASE_MARKER"] == '<%= ENV.fetch("MAXIBOT_COMMIT_SHA") %>'
        assert release_env["MAXIBOT_COMMIT_SHA"] == '<%= ENV.fetch("MAXIBOT_COMMIT_SHA") %>'


def test_kamal_uses_existing_secret_files_and_uploads_volume():
    config = yaml.safe_load((ROOT / "config/deploy.yml").read_text())
    for role in config["servers"].values():
        options = role["options"]
        assert "/opt/maxibot/.env" in options["env-file"]
        assert "/opt/maxibot/secrets/mail_cloud.env" in options["env-file"]
    assert config["servers"]["api"]["options"]["volume"] == "maxibot-vps_maxibot_uploads:/app/uploads"


def test_kamal_workflow_is_github_native_and_owner_authorized():
    workflow = WORKFLOW.read_text()

    assert "runs-on: ubuntu-latest" in workflow
    assert "runs-on: [self-hosted" not in workflow
    assert "environment:\n      name: production" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "TASK_SCOPED_AUTO" not in workflow
    assert "claimed" not in workflow and "consumed" not in workflow
    assert "contents: write" not in workflow
    assert "deployments: write" in workflow


def test_ci_build_and_verified_run_scoped_digest_precede_kamal_deploy():
    workflow = WORKFLOW.read_text()

    assert "needs: tests" in workflow
    assert "steps.image.outputs.digest" in workflow
    assert "value=$CANDIDATE_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" in workflow
    assert "actions/upload-artifact" not in workflow
    assert "APPROVED_DIGEST" in workflow
    assert 'bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles api' in workflow
    assert 'bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles worker' in workflow
    assert 'bundle exec kamal app stop --roles worker' in workflow
