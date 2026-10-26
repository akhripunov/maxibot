from pathlib import Path
import json
import re
import subprocess

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
    assert 'case "$CREDENTIAL_ROTATION_ATTESTATION" in' in preflight
    assert "ROTATED)" in preflight
    assert "OWNER_ACCEPTED_UNROTATED)" in preflight
    assert '.name == "tests" and .conclusion == "success"' in preflight
    assert '.name == "build" and .conclusion == "success"' in preflight
    assert "StrictHostKeyChecking=yes" in preflight
    assert "StrictHostKeyChecking yes" in preflight
    assert 'install -m 600 "$RUNNER_TEMP/ssh/id_ed25519" "$HOME/.ssh/id_ed25519"' in preflight
    assert 'UserKnownHostsFile $HOME/.ssh/known_hosts' in preflight
    assert "MAXIBOT_PRODUCTION_SSH_KEY" in preflight
    assert "MAXIBOT_PRODUCTION_KNOWN_HOSTS" in preflight
    assert "repos/$GITHUB_REPOSITORY/environments/production" in preflight
    assert "required_reviewers" in preflight
    assert "openssl s_client" in preflight
    for host in ("maxibot.pro", "www.maxibot.pro", "api.maxibot.pro", "go.maxibot.pro", "admin.maxibot.pro"):
        assert host in preflight
    assert "docker ps -q --filter \"publish=$port\"" in preflight
    assert "pg_restore --list" in preflight
    assert "cutover_restore_probe" in preflight
    assert '"$IMAGE_NAME@$attestation_digest" --raw' in preflight
    assert 'platform_manifest_digest="$(jq -er' in preflight
    assert '"vnd.docker.reference.digest"] == $subject' in preflight
    assert 'in-toto.io/predicate-type' in preflight
    assert 'https://spdx.dev/Document' in preflight
    assert 'https://slsa.dev/provenance/v1' in preflight
    assert "descriptor_count >= 2" not in preflight
    assert 'select(.platform.os == "unknown" and .platform.architecture == "unknown")' in preflight
    assert "free_bytes" in preflight
    assert "restore_data_dir" in preflight
    assert "--network none" in preflight
    assert "--tmpfs /var/lib/postgresql/data" not in preflight
    assert "rm -rf -- \"$restore_data_dir\"" in preflight
    assert "trap cleanup EXIT" in preflight
    assert 'docker rm -f "$restore_name"' in preflight
    assert "docker info" in preflight
    assert '--mount "type=bind,src=$restore_data_dir,dst=/var/lib/postgresql/data"' in preflight
    assert '"$restored_head" = "$head"' in preflight
    assert '"$restored_tables" = "$source_tables"' in preflight
    assert '"$restored_extensions" = "$source_extensions"' in preflight
    assert "docker build-push-action@" not in workflow
    assert "docker compose up" not in workflow
    assert "kamal deploy" not in preflight


def test_owner_can_explicitly_accept_unrotated_credentials_without_rotation_claim():
    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)
    rotation_input = workflow["on"]["workflow_dispatch"]["inputs"]["credential_rotation_attestation"]
    assert "ROTATED" in rotation_input["options"]
    assert "OWNER_ACCEPTED_UNROTATED" in rotation_input["options"]
    assert "NOT_CONFIRMED" in rotation_input["options"]

    preflight = "\n".join(
        step.get("run", "")
        for step in workflow["jobs"]["preflight"]["steps"]
    )
    assert "OWNER_ACCEPTED_UNROTATED" in preflight
    assert "Owner explicitly accepted the unrotated-credentials risk" in preflight
    assert "credentials were rotated" not in preflight


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
    assert "docker start maxibot-vps-api-1" in apply
    assert "docker start maxibot-vps-https-1" in apply
    assert "IMAGE_DIGEST" in apply and "RepoDigests" in apply
    assert "EXPECTED_DATABASE_HEAD" in apply
    assert "EXPECTED_CADDY_ID" in apply
    assert "StrictHostKeyChecking=yes" in apply


def test_oci_attestation_predicates_accept_one_or_multiple_descriptors_but_require_both_types():
    workflow = WORKFLOW_PATH.read_text()
    preflight = workflow.split("\n  preflight:\n", 1)[1].split("\n  apply:\n", 1)[0]
    descriptor_filter = re.search(
        r'mapfile -t attestation_digests < <\(jq -r --arg subject "\$platform_manifest_digest" \'\n(.*?)\n\s*\' <<< "\$raw_index"\)',
        preflight,
        re.DOTALL,
    )
    assert descriptor_filter

    def selected_digests(index, subject):
        result = subprocess.run(
            ["jq", "-r", "--arg", "subject", subject, descriptor_filter.group(1)],
            input=json.dumps(index),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.splitlines()

    platform_digest = "sha256:" + "1" * 64
    attestation_digest = "sha256:" + "2" * 64
    unrelated_digest = "sha256:" + "3" * 64
    index = {
        "manifests": [
            {"platform": {"os": "linux", "architecture": "amd64"}, "digest": platform_digest},
            {
                "platform": {"os": "unknown", "architecture": "unknown"},
                "digest": attestation_digest,
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": platform_digest,
                },
            },
            {
                "platform": {"os": "unknown", "architecture": "unknown"},
                "digest": unrelated_digest,
                "annotations": {
                    "vnd.docker.reference.type": "attestation-manifest",
                    "vnd.docker.reference.digest": unrelated_digest,
                },
            },
        ]
    }
    assert selected_digests(index, platform_digest) == [attestation_digest]

    predicate_filters = re.findall(
        r"jq -e '(any\(\.layers\[\]\?; \.annotations\[\"in-toto\.io/predicate-type\"\] == \"https://(?:spdx\.dev/Document|slsa\.dev/provenance/v1)\"\))'",
        preflight,
    )
    assert len(predicate_filters) == 2

    def predicate_present(jq_filter, layers):
        return subprocess.run(
            ["jq", "-e", jq_filter],
            input=json.dumps({"layers": layers}),
            text=True,
            capture_output=True,
            check=False,
        ).returncode == 0

    required_types = (
        "https://spdx.dev/Document",
        "https://slsa.dev/provenance/v1",
    )
    filters_by_type = {
        next(value for value in required_types if value in expression): expression
        for expression in predicate_filters
    }
    single_descriptor_layers = [
        {"annotations": {"in-toto.io/predicate-type": value}}
        for value in required_types
    ]
    for predicate_type in required_types:
        assert predicate_present(filters_by_type[predicate_type], single_descriptor_layers)

    split_descriptor_layers = [
        [{"annotations": {"in-toto.io/predicate-type": predicate_type}}]
        for predicate_type in required_types
    ]
    for index, predicate_type in enumerate(required_types):
        assert predicate_present(filters_by_type[predicate_type], split_descriptor_layers[index])
        assert not predicate_present(filters_by_type[predicate_type], split_descriptor_layers[1 - index])


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
    assert "docker stop maxibot-vps-https-1" in apply
    assert "bundle exec kamal proxy stop" in apply
    assert "80/tcp" in apply and "443/tcp" in apply
    assert "assert not any('caddy' in (x['Name']+x['Config'].get('Image','')).lower()" in apply
    assert "--resolve \"$host:443:$PRODUCTION_HOST\"" in apply
    assert "--resolve \"$host:80:$PRODUCTION_HOST\"" in apply
    assert "strict-transport-security:" in apply
    assert "content-security-policy:" in apply
    assert "x-content-type-options: nosniff" in apply
    assert "inputs.confirm_apply == 'APPLY_FIRST_COMPOSE_TO_KAMAL'" in workflow["jobs"]["apply"]["if"]


def test_environment_readback_uses_supported_permission_and_actual_github_response_shape():
    workflow_text = WORKFLOW_PATH.read_text()
    workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)

    for job_name in ("preflight", "apply"):
        permissions = workflow["jobs"][job_name]["permissions"]
        assert permissions["actions"] == "read"
        assert "administration" not in permissions
        commands = "\n".join(step.get("run", "") for step in workflow["jobs"][job_name]["steps"])
        assert "repos/$GITHUB_REPOSITORY/environments/production" in commands

    filters = re.findall(
        r"""jq -e --arg owner .*? '
(.*?)
\s*' <<< "\$environment" >/dev/null""",
        workflow_text,
        re.DOTALL,
    )
    assert len(filters) == 2

    environment = {
        "prevent_self_review": None,
        "can_admins_bypass": True,
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": False,
                "reviewers": [{"type": "User", "reviewer": {"login": "akhripunov"}}],
            },
            {"type": "branch_policy", "reviewers": []},
        ],
    }
    for jq_filter in filters:
        def passes(response):
            return subprocess.run(
                ["jq", "-e", "--arg", "owner", "akhripunov", jq_filter],
                input=json.dumps(response),
                text=True,
                capture_output=True,
                check=False,
            ).returncode == 0

        assert passes(environment)
        environment["protection_rules"][0]["prevent_self_review"] = True
        assert not passes(environment)
        environment["protection_rules"][0]["prevent_self_review"] = False

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


def test_build_to_dispatch_diff_allows_cutover_docs_and_blocks_runtime_changes():
    workflow = WORKFLOW_PATH.read_text()
    filters = re.findall(r"jq -e '\n(.*?)\n\s*' <<< \"\$main_comparison\"", workflow, re.DOTALL)
    allowed_doc_diff = {
        "status": "ahead",
        "files": [{"filename": "docs/KAMAL_FIRST_CUTOVER.md"}],
    }
    blocked_runtime_diff = {
        "status": "ahead",
        "files": [{"filename": "app/main.py"}],
    }

    assert len(filters) == 4  # Build selection, read-only checks, Apply, and pre-mutation recheck.
    for jq_filter in filters:
        allowed = subprocess.run(
            ["jq", "-e", jq_filter],
            input=json.dumps(allowed_doc_diff),
            text=True,
            capture_output=True,
            check=False,
        )
        assert allowed.returncode == 0, allowed.stderr
        blocked = subprocess.run(
            ["jq", "-e", jq_filter],
            input=json.dumps(blocked_runtime_diff),
            text=True,
            capture_output=True,
            check=False,
        )
        assert blocked.returncode != 0


def test_actions_api_build_path_is_matched_without_ref_suffix_in_both_jobs():
    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)
    api_response = {
        "head_sha": "4b88530bc6334f63f879740603945c4f1a3056b6",
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/maxibot-native-release.yml",
    }
    steps = {
        step.get("name"): step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job["steps"]
    }
    build_steps = (
        steps["Verify OWNER attestation and exact successful Build image"],
        steps["Reconcile exact Compose baseline and current main before any Apply"],
    )

    for run in build_steps:
        path_match = re.search(r'\.path\s*==\s*"([^"]+)"', run)
        assert path_match and path_match.group(1) == api_response["path"]
        assert subprocess.run(
            ["jq", "-e", f'.path == "{path_match.group(1)}"'],
            input=json.dumps(api_response), text=True, capture_output=True, check=False,
        ).returncode == 0

    api_response["path"] += "@refs/heads/main"
    assert subprocess.run(
        ["jq", "-e", f'.path == "{path_match.group(1)}"'],
        input=json.dumps(api_response), text=True, capture_output=True, check=False,
    ).returncode != 0


def test_competing_run_filter_rejects_release_workflows_but_ignores_unrelated_workflows():
    workflow = WORKFLOW_PATH.read_text()
    filters = re.findall(
        r'jq -e --argjson current "\$GITHUB_RUN_ID" \'([^\']+)\' <<< "\$active"',
        workflow,
    )
    assert len(filters) == 2
    assert filters[0] == filters[1]
    def no_competitor(runs):
        return subprocess.run(
            ["jq", "-e", "--argjson", "current", "10", filters[0]],
            input=json.dumps({"workflow_runs": runs}),
            text=True,
            capture_output=True,
            check=False,
        ).returncode == 0

    assert no_competitor([{"id": 11, "path": ".github/workflows/unrelated.yml"}])
    assert no_competitor([{"id": 10, "path": ".github/workflows/maxibot-native-release.yml"}])
    assert not no_competitor([{"id": 11, "path": ".github/workflows/maxibot-native-release.yml"}])
    assert not no_competitor([{"id": 11, "path": ".github/workflows/maxibot-first-kamal-cutover.yml"}])


def test_exact_diff_is_rechecked_immediately_before_first_apply_mutation():
    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)
    apply_steps = workflow["jobs"]["apply"]["steps"]
    mutation_step = next(step for step in apply_steps if step.get("name") == "Prepare DB, Redis and Compose API recovery connectivity without removing existing networks")
    run = mutation_step["run"]

    compare_check = run.index('gh api "repos/$GITHUB_REPOSITORY/compare/$CANDIDATE_SHA...$main_sha"')
    jq_check = run.index("jq -e '")
    production_ssh = run.index('ssh -i "$PRODUCTION_SSH"')
    network_mutation = run.index("docker network connect --alias")

    assert mutation_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert mutation_step["env"]["CANDIDATE_SHA"] == "${{ inputs.candidate }}"
    assert compare_check < jq_check < production_ssh < network_mutation


def test_kamal_registry_password_is_loaded_from_ephemeral_secrets_file_for_each_kamal_step():
    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)
    steps = {
        step.get("name"): step
        for step in workflow["jobs"]["apply"]["steps"]
    }
    kamal_steps = (
        "Set up Kamal and confirm resolved configuration",
        "Stop Compose Worker, API and Caddy in order; release ports 80/443 to Kamal Proxy",
        "Start the sole Kamal Worker and verify runtime invariants",
    )

    for name in kamal_steps:
        run = steps[name]["run"]
        assert 'install -d -m 700 .kamal' in run
        assert 'umask 077' in run
        assert "KAMAL_REGISTRY_PASSWORD=%s" in run
        assert '"$KAMAL_REGISTRY_PASSWORD" > .kamal/secrets' in run
        assert 'trap \'rm -f .kamal/secrets\' EXIT' in run

def test_remote_preflight_reports_sanitized_gate_name_on_failure():
    workflow = WORKFLOW_PATH.read_text()
    preflight = workflow.split("\n  preflight:\n", 1)[1].split("\n  apply:\n", 1)[0]
    remote = preflight.split("<<'REMOTE'\n", 1)[1].split("\n          REMOTE", 1)[0]

    trap_command = next(line.strip() for line in remote.splitlines() if line.strip().startswith("trap 'status=$?;"))
    simulation = subprocess.run(
        ["bash", "-c", "set -euo pipefail\ngate=synthetic_gate\n" + trap_command + "\nfalse"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert simulation.returncode == 1
    assert simulation.stderr.strip() == "PREFLIGHT_REMOTE_GATE_FAILED=synthetic_gate status=1"

    for gate in (
        "compose_containers_running",
        "caddy_owns_public_ports",
        "kamal_proxy_absent",
        "single_poller_and_network_topology",
        "isolated_restore_name_available",
        "isolated_restore_capacity",
        "isolated_restore_create_resources",
        "isolated_restore_consistency",
        "persistent_volume_identity",
    ):
        assert f"gate={gate}" in remote
    assert "PRODUCTION_SSH_KEY" not in remote
    assert "KAMAL_REGISTRY_PASSWORD" not in remote
