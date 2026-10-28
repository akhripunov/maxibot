from pathlib import Path
import subprocess
import os
import re
import ast
import json

import pytest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/maxibot-native-release.yml"


def test_kamal_release_automatically_deploys_main_after_tests_and_build():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)

    assert "main" in workflow["on"]["push"]["branches"]
    assert workflow["on"]["pull_request"]["branches"] == ["main"]
    operation = workflow["on"]["workflow_dispatch"]["inputs"]["operation"]
    assert operation["options"] == ["rollback"]
    assert operation["default"] == "rollback"
    assert workflow["on"]["workflow_dispatch"]["inputs"]["candidate"]["required"] == "true"
    assert workflow["on"]["workflow_dispatch"]["inputs"]["build_run_id"]["required"] == "true"
    assert workflow["on"]["workflow_dispatch"]["inputs"]["image_digest"]["required"] == "true"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert list(workflow["jobs"]) == ["tests", "build", "deploy"]
    assert workflow["jobs"]["build"]["needs"] == "tests"
    assert "workflow_dispatch" not in workflow["jobs"]["build"]["if"]
    assert "github.event_name == 'push'" in workflow["jobs"]["build"]["if"]
    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == ["tests", "build"]
    assert "always()" in deploy["if"]
    assert "github.event_name == 'push'" in deploy["if"]
    assert "github.ref == 'refs/heads/main'" in deploy["if"]
    assert "needs.tests.result == 'success'" in deploy["if"]
    assert "needs.build.result == 'success'" in deploy["if"]
    assert "inputs.operation == 'rollback'" in deploy["if"]
    assert "pull_request" not in deploy["if"]
    assert "codex/native-release-maxibot" not in deploy["if"]
    assert "inputs.mode" not in workflow["jobs"]["build"]["if"]
    assert deploy["environment"]["name"] == "production"
    assert deploy["permissions"]["packages"] == "read"
    assert deploy["permissions"]["actions"] == "read"


def test_tests_job_loads_pinned_kamal_config_with_synthetic_secret_without_deploy_access():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    tests = workflow["jobs"]["tests"]
    setup = next(step for step in tests["steps"] if step.get("uses", "").startswith("ruby/setup-ruby@"))
    verify = next(step for step in tests["steps"] if step.get("name") == "Load Kamal config with synthetic registry secret")

    assert setup["uses"] == "ruby/setup-ruby@95ef2b042f9d7a56d8268cba8559e2842e2ad01b"
    assert setup["with"]["ruby-version"] == "3.3.6"
    assert setup["with"]["bundler-cache"] == "true"
    assert tests["env"]["BUNDLE_GEMFILE"] == "Gemfile.kamal"
    lockfile = (ROOT / "Gemfile.kamal.lock").read_text()
    assert "kamal (2.12.0)" in lockfile
    assert "KAMAL_REGISTRY_PASSWORD" in verify["run"]
    assert "secrets.token_hex(32)" in verify["run"]
    assert "unset synthetic_registry_secret" in verify["run"]
    assert "chmod 600 .kamal/secrets" in verify["run"]
    assert "git check-ignore -q .kamal/secrets" in verify["run"]
    assert "trap 'rm -f .kamal/secrets' EXIT" in verify["run"]
    assert "test ! -e .kamal/secrets" in verify["run"]
    assert "bundle exec kamal config" in verify["run"]
    assert "kamal registry login" not in verify["run"]
    assert "kamal deploy" not in verify["run"]
    assert "kamal app stop" not in verify["run"]
    assert "ssh " not in verify["run"]
    subprocess.run(["git", "check-ignore", "-q", ".kamal/secrets"], cwd=ROOT, check=True)
    assert workflow["jobs"]["build"]["needs"] == "tests"


def test_every_normal_deploy_kamal_step_creates_private_secrets_file_and_cleans_it():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    deploy = workflow["jobs"]["deploy"]
    assert deploy["env"]["KAMAL_REGISTRY_PASSWORD"] == "${{ github.token }}"
    steps = deploy["steps"]
    kamal_calls = {
        "Verify exact GHCR image and Kamal configuration": (
            "bundle exec kamal config",
            "bundle exec kamal registry login",
        ),
        "Deploy application with Kamal": ("bundle exec kamal deploy --skip-push --version \"$IMAGE_VERSION\" --roles api",),
        "Stop the existing Kamal Worker before starting its replacement": (
            "bundle exec kamal app stop --roles worker",
        ),
        "Deploy the sole Worker with Kamal": ("bundle exec kamal deploy --skip-push --version \"$IMAGE_VERSION\" --roles worker",),
    }
    for name, calls in kamal_calls.items():
        step = next(step for step in steps if step.get("name") == name)
        run = step["run"]
        assert all(call in run for call in calls)
        assert "install -d -m 700 .kamal" in run
        assert "umask 077" in run
        assert 'printf \'KAMAL_REGISTRY_PASSWORD=%s\\n\' "$KAMAL_REGISTRY_PASSWORD" > .kamal/secrets' in run
        assert "chmod 600 .kamal/secrets" in run
        assert "git check-ignore -q .kamal/secrets" in run
        assert "trap 'rm -f .kamal/secrets' EXIT" in run


def test_kamal_build_is_run_scoped_and_records_source_sha_and_digest():
    document = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    workflow = WORKFLOW.read_text()

    assert "value=$CANDIDATE_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" in workflow
    assert "tags: ghcr.io/akhripunov/maxibot-vps-api:${{ steps.version.outputs.value }}" in workflow
    assert "org.opencontainers.image.revision=${{ env.CANDIDATE_SHA }}" in workflow
    assert "steps.image.outputs.digest" in workflow
    assert 'MAXIBOT_BUILD_RUN_ID=$GITHUB_RUN_ID' in workflow
    assert 'MAXIBOT_BUILD_RUN_ATTEMPT=$GITHUB_RUN_ATTEMPT' in workflow
    assert document["jobs"]["build"]["outputs"]["image_digest"] == "${{ steps.image.outputs.digest }}"
    assert document["jobs"]["build"]["outputs"]["image_version"] == "${{ steps.version.outputs.value }}"
    assert "actions/upload-artifact@" not in workflow
    assert "actions/download-artifact@" not in workflow
    assert "packages: write" in workflow


def test_kamal_deploy_uses_same_run_build_outputs_and_verifies_exact_digest():
    workflow = WORKFLOW.read_text()
    document = yaml.load(workflow, Loader=yaml.BaseLoader)
    deploy_job = document["jobs"]["deploy"]
    deploy = "\n".join(step.get("run", "") for step in deploy_job["steps"])

    assert 'actions/runs/$BUILD_RUN_ID' in deploy
    assert '.head_sha == $sha' in deploy
    assert '.head_branch == "main"' in deploy
    assert '.event == "push"' in deploy
    assert '.status == "completed"' in deploy
    assert '.conclusion == "success"' in deploy
    assert '.path == ".github/workflows/maxibot-native-release.yml"' in deploy
    assert 'image_version="$CANDIDATE_SHA-$BUILD_RUN_ID-$build_attempt"' in deploy
    assert "needs.build.outputs.image_digest" in str(deploy_job)
    assert "github.run_id" in deploy_job["env"]["BUILD_RUN_ID"]
    build_selection = re.search(
        r'if \[\[ "\$RELEASE_OPERATION" == deploy \]\]; then(.*?)\n\s*else\n(.*?)\n\s*fi',
        deploy,
        re.S,
    )
    assert build_selection
    same_run, rollback_build = build_selection.groups()
    assert 'test "$BUILD_RUN_ID" = "$GITHUB_RUN_ID"' in same_run
    assert 'build_attempt="${BUILD_IMAGE_VERSION##*-}"' in same_run
    assert '.run_attempt >= ($attempt | tonumber)' in same_run
    assert 'build_attempt="$GITHUB_RUN_ATTEMPT"' not in same_run
    assert '.status == "in_progress"' in same_run
    assert '.status == "completed"' in rollback_build
    assert '.conclusion == "success"' in rollback_build
    assert 'APPROVED_DIGEST' in deploy
    assert 'test "$image_digest" = "$APPROVED_DIGEST"' in deploy
    assert 'git/ref/heads/main' in deploy
    assert 'docker buildx imagetools inspect "$IMAGE_TAG"' in deploy
    assert 'docker pull "$IMAGE_REF"' in deploy
    assert "docker/build-push-action@" not in deploy
    deploy_workflow = workflow.split("\n  deploy:\n", 1)[1]
    assert deploy_workflow.index("Select and verify the exact-SHA build image") < deploy_workflow.index("actions/checkout@")
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
    assert 'kamal-proxy' in deploy
    assert 'maxibot-vps-https-1' not in deploy
    assert "for host in maxibot.pro www.maxibot.pro api.maxibot.pro go.maxibot.pro admin.maxibot.pro" in deploy
    assert '"https://$host/health/ready"' in deploy
    assert "content-security-policy:" in deploy
    assert "strict-transport-security:" in deploy
    assert "x-frame-options: DENY" in deploy
    assert "x-content-type-options: nosniff" in deploy
    assert "referrer-policy: no-referrer" in deploy
    assert "cache-control: no-store" in deploy
    assert 'assert not old["State"]["Running"]' in deploy
    assert 'assert not any("caddy"' not in deploy
    assert 'bundle exec kamal rollback "$IMAGE_VERSION" --roles api' in deploy
    assert 'bundle exec kamal rollback "$IMAGE_VERSION" --roles worker' in deploy


def test_ordinary_kamal_deploy_uses_runtime_baseline_without_cutover_backup_restore_or_caddy():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["deploy"]["steps"]
    deploy_script = "\n".join(step.get("run", "") for step in steps)
    baseline = next(step for step in steps if step.get("name") == "Verify exact main and read the current production baseline")["run"]
    kamal_config = next(step for step in steps if step.get("name") == "Verify exact GHCR image and Kamal configuration")["run"]
    runtime = next(step for step in steps if step.get("name") == "Verify runtime, network connectivity, and Worker heartbeat")["run"]

    assert "api_compose" not in baseline and "worker_compose" not in baseline
    assert "db=maxibot-vps-db-1" in baseline and 'require_running "$db"' in baseline
    assert "redis=maxibot-vps-redis-1" in baseline and 'require_running "$redis"' in baseline
    assert "redis-cli ping" in baseline and '[[ "$redis_ping" == PONG ]]' in baseline
    assert "health/ready" in baseline
    assert "[[ \"$api_sha\" == \"$worker_sha\" ]]" in baseline
    assert "alembic_version" in baseline
    assert "old_db_volume" in baseline and "old_redis_volume" in baseline and "old_uploads_volume" in baseline
    assert "MAXIBOT_BACKUP_MAX_AGE_SECONDS" not in deploy_script
    assert "pg_restore" not in deploy_script
    assert "native_restore_probe" not in deploy_script
    assert "maxibot-native-restore" not in deploy_script
    assert "caddy" not in deploy_script.lower()
    assert "maxibot-vps-https-1" not in deploy_script
    assert "BASELINE_MODE" not in kamal_config
    assert "FIRST_COMPOSE_TO_KAMAL_CUTOVER_REQUIRED" not in kamal_config
    assert "EXPECTED_CADDY_ID" not in runtime
    assert "kamal-proxy" in runtime
    assert "PRODUCTION_BASELINE=FAIL:" in baseline
    assert "Redis PING command failed" in baseline
    assert 'bash scripts/frozen_path_guard.sh --base "$baseline_sha" --head "$CANDIDATE_SHA"' in baseline
    assert "--owner-approved-task ME-53" not in baseline


def test_ordinary_kamal_release_keeps_backup_restore_separate_from_normal_deploy():
    cutover = (ROOT / ".github/workflows/maxibot-first-kamal-cutover.yml").read_text()

    assert "pg_restore --list" in cutover
    assert "cutover_restore_probe" in cutover
    assert "--network none" in cutover


def test_kamal_config_uses_kamal_proxy_and_existing_data_network():
    config = yaml.safe_load((ROOT / "config/deploy.yml").read_text())
    compose = (ROOT / "docker-compose.vps.yml").read_text()

    assert config["proxy"]["ssl"] is True
    assert set(config["proxy"]["hosts"]) == {"maxibot.pro", "www.maxibot.pro", "api.maxibot.pro", "go.maxibot.pro", "admin.maxibot.pro"}
    assert "network" not in config["servers"]["api"]["options"]
    assert config["servers"]["api"]["options"]["network-alias"] == "api"
    assert "network" not in config["servers"]["worker"]["options"]
    assert "name: maxibot-vps-internal" in compose
    assert "name: maxibot-vps-frontend" in compose
    assert "accessories" not in config
    assert config["servers"]["api"]["options"]["volume"] == "maxibot-vps_maxibot_uploads:/app/uploads"
    assert config["servers"]["worker"]["options"]["memory"] == "512m"
    assert config["servers"]["worker"]["proxy"] is False


@pytest.mark.parametrize('ids,status,ok', [('a'*64,0,True),('',0,False),('a'*64+'\n'+'b'*64,0,False),('',42,False)])
def test_normal_kamal_selection_provides_exact_inspect_ids_and_rejects_docker_failures(ids,status,ok):
    workflow=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    steps=workflow['jobs']['deploy']['steps']
    runs=[s['run'] for s in steps if 'run' in s and 'mapfile -t apis' in s['run'] or 'run' in s and 'api="$(docker ps ' in s['run']]
    assert len(runs)==3
    for run in runs:
        blocks=re.findall(r'api="\$\(docker ps [^\n]+\n\s*worker="\$\(docker ps [^\n]+\n\s*\[\[ [^\n]+',run)
        assert len(blocks)==1, 'Selections must propagate Docker errors and require one full ID'
        fake='docker() { [[ "$*" == *--no-trunc* ]] || { printf "%s" "${IDS:0:12}"; return 0; }; printf "%s" "$IDS"; return "$STATUS"; }'
        r=subprocess.run(['bash','-c','set -euo pipefail\n'+fake+'\n'+blocks[0]],env={**os.environ,'IDS':ids,'STATUS':str(status)},capture_output=True)
        assert (r.returncode==0) is ok


def test_normal_kamal_ssh_uses_the_same_strict_identity_as_read_only_preflight():
    doc=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    run=next(s['run'] for s in doc['jobs']['deploy']['steps'] if s.get('name')=='Verify exact main and read the current production baseline')
    assert 'install -m 600 "$RUNNER_TEMP/ssh/id_ed25519" "$HOME/.ssh/id_ed25519"' in run
    assert 'IdentityFile $HOME/.ssh/id_ed25519' in run
    assert 'IdentitiesOnly yes' in run and 'BatchMode yes' in run and 'StrictHostKeyChecking yes' in run


def test_external_build_sets_kamal_service_label_and_deploy_verifies_it():
    workflow=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    build=next(s for s in workflow['jobs']['build']['steps'] if s.get('uses','').startswith('docker/build-push-action@'))
    assert 'service=maxibot' in build['with']['labels'].splitlines()
    deploy='\n'.join(s.get('run','') for s in workflow['jobs']['deploy']['steps'])
    assert deploy.count('test "$service_label" = maxibot')==2


def test_every_normal_kamal_step_inherits_the_dispatch_source_sha():
    doc=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    deploy=doc['jobs']['deploy']
    for step in deploy['steps']:
        if 'bundle exec kamal ' in step.get('run',''):
            assert step['env']['MAXIBOT_COMMIT_SHA']=='${{ env.CANDIDATE_SHA }}'


def test_nested_docker_python_commands_compile_before_any_release():
    doc=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    checked=0
    for step in doc['jobs']['deploy']['steps']:
        for body in re.findall(r"<<'PY'\n(.*?)\nPY",step.get('run',''),re.S):
            tree=ast.parse(body)
            for node in ast.walk(tree):
                if not isinstance(node,ast.Call) or not node.args or not isinstance(node.args[0],ast.List): continue
                args=node.args[0].elts
                for i,arg in enumerate(args[:-1]):
                    if i and isinstance(args[i-1],ast.Constant) and args[i-1].value in ('python','python3') and isinstance(arg,ast.Constant) and arg.value=='-c' and isinstance(args[i+1],ast.Constant):
                        compile(args[i+1].value,'nested docker python -c','exec')
                        checked+=1
    assert checked>=3


def test_rollback_is_explicit_and_executes_trusted_current_main_workflow():
    doc=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    operation=doc['on']['workflow_dispatch']['inputs']['operation']
    assert operation['options']==['rollback']
    assert operation['default']=='rollback'
    checkout=next(s for s in doc['jobs']['deploy']['steps'] if s.get('uses','').startswith('actions/checkout@'))
    assert checkout['with']['ref']=='${{ github.sha }}'
    source=WORKFLOW.read_text()
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in source
    assert 'git merge-base --is-ancestor "$CANDIDATE_SHA" "$baseline_sha"' in source
    assert 'git diff --quiet "$CANDIDATE_SHA" "$baseline_sha" -- app alembic config .kamal Dockerfile Gemfile.kamal Gemfile.kamal.lock pyproject.toml' in source
    assert 'ROLLBACK_PREVIOUS_SUCCESS=PASS' in source
    assert 'ROLLBACK_RETAINED_IMAGE=PASS' in source


def test_rollback_target_must_be_latest_successful_other_production_version(tmp_path):
    import json
    doc=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    source=next(s['run'] for s in doc['jobs']['deploy']['steps'] if s.get('id')=='baseline')
    gate=source.split('# ROLLBACK_DEPLOYMENT_GATE_BEGIN\n',1)[1].split('# ROLLBACK_DEPLOYMENT_GATE_END',1)[0]
    gh=tmp_path/'gh'
    gh.write_text('#!/usr/bin/env python3\nimport os,json,sys\nroute=sys.argv[2]\nprint(json.dumps(json.loads(os.environ["DEPLOYMENTS"]) if "statuses" not in route else json.loads(os.environ["STATUSES"])[route.split("/")[-2]]))\n')
    gh.chmod(0o700)
    a='a'*40; b='b'*40; c='c'*40
    base={**os.environ,'PATH':str(tmp_path)+os.pathsep+os.environ['PATH'],'GITHUB_REPOSITORY':'akhripunov/maxibot','RELEASE_OPERATION':'rollback','baseline_sha':a,'CANDIDATE_SHA':b}
    def run(deployments,statuses):
        return subprocess.run(['bash','-euo','pipefail','-c',gate],env={**base,'DEPLOYMENTS':json.dumps(deployments),'STATUSES':json.dumps(statuses)},capture_output=True,text=True)
    assert run([{'id':1,'sha':b}],{'1':[{'state':'success'}]}).returncode==0
    assert run([{'id':1,'sha':b}],{'1':[{'state':'failure'}]}).returncode!=0
    assert run([{'id':2,'sha':c},{'id':1,'sha':b}],{'2':[{'state':'success'}],'1':[{'state':'success'}]}).returncode!=0
    assert run([{'id':3,'sha':a},{'id':1,'sha':b}],{'3':[{'state':'success'}],'1':[{'state':'inactive'},{'state':'success'}]}).returncode==0
    assert run([],{}).returncode!=0


def test_deployment_identity_and_status_are_exact_and_fail_closed(tmp_path):
    doc=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    deploy=doc['jobs']['deploy']
    assert deploy['environment']['deployment']=='false'
    create=next(s for s in deploy['steps'] if s.get('id')=='deployment')
    assert 'ref:$ref' in create['run'] and '"$CANDIDATE_SHA"' in create['run']
    assert 'image:$digest' in create['run'] and '"$IMAGE_REF"' in create['run']
    result=deploy['steps'][-1]
    assert result['if']=="always() && steps.deployment.outputs.id != ''"
    gh=tmp_path/'gh'; output=tmp_path/'arguments'
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    gh.chmod(0o700)
    env={**os.environ,'PATH':str(tmp_path)+os.pathsep+os.environ['PATH'],'CAPTURE':str(output),'DEPLOYMENT_ID':'123','RELEASE_OPERATION':'rollback','CANDIDATE_SHA':'a'*40,'GITHUB_REPOSITORY':'akhripunov/maxibot','GITHUB_SERVER_URL':'https://github.com','GITHUB_RUN_ID':'456'}
    for status in ['success','failure','cancelled','skipped']:
        subprocess.run(['bash','-euo','pipefail','-c',result['run']],env={**env,'JOB_RESULT':status},check=True)
        args=output.read_text().splitlines()
        assert ('state=success' if status=='success' else 'state=failure') in args


def test_worker_observation_covers_one_primary_poll_timeout_and_recovery_cycle():
    doc=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    verify=next(s for s in doc['jobs']['deploy']['steps'] if s.get('name')=='Verify runtime, network connectivity, and Worker heartbeat')
    assert '--interval 60 --max-age 90' in verify['run']


def test_pinned_rollback_interface_uses_loaded_cli_metadata_not_help_text():
    doc=yaml.load(WORKFLOW.read_text(),Loader=yaml.BaseLoader)
    verify=next(s for s in doc['jobs']['tests']['steps'] if s.get('name')=='Load Kamal config with synthetic registry secret')
    assert 'Main.class_options.keys.map(&:to_s).include?("roles")' in verify['run']
    assert 'Main.all_commands.key?("rollback")' in verify['run']
    assert 'kamal rollback --help | grep' not in verify['run']


def test_isolated_cutover_restore_cleanup_remains_fail_closed_and_never_prunes_volumes():
    cutover = (ROOT / ".github/workflows/maxibot-first-kamal-cutover.yml").read_text()

    assert 'docker rm -f "$restore_name"' in cutover
    assert "cleanup_failed=1" in cutover
    assert "docker volume prune" not in cutover
    assert "cutover_restore_probe" in cutover


def _run_build_selection(workflow_text, tmp_path, *, run_attempt, build_attempt, candidate_sha=None, build_sha=None, approved_digest=None, manifest_digest=None):
    import json
    document = yaml.load(workflow_text, Loader=yaml.BaseLoader)
    step = next(item for item in document["jobs"]["deploy"]["steps"] if item.get("name") == "Select and verify the exact-SHA build image")
    sha = candidate_sha or "a" * 40
    build_image_sha = build_sha or sha
    digest = approved_digest or ("sha256:" + "1" * 64)
    actual_digest = manifest_digest or digest
    build_run_id = "40597659419"
    (tmp_path / "build-run.json").write_text(json.dumps({
        "head_sha": sha, "head_branch": "main", "event": "push",
        "run_attempt": run_attempt, "path": ".github/workflows/maxibot-native-release.yml",
        "status": "in_progress",
    }))
    gh = tmp_path / "gh"
    gh.write_text('#!/usr/bin/env bash\nif [[ "$2" == *git/ref/heads/main* ]]; then printf "%s\\n" "$MAIN_SHA"; else cat "$BUILD_RUN_JSON"; fi\n')
    gh.chmod(0o700)
    jq = tmp_path / "jq"
    jq.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nargs=sys.argv[1:]\ndata=json.load(sys.stdin)\n"
        "if args[0]=='-r': print(data['run_attempt']); raise SystemExit(0)\n"
        "sha=args[args.index('--arg')+2]\n"
        "attempt=int(args[args.index('--arg', args.index('--arg')+1)+2])\n"
        "ok=(data.get('head_sha')==sha and data.get('head_branch')=='main' and data.get('event')=='push' "
        "and data.get('run_attempt',0)>=attempt and data.get('path')=='.github/workflows/maxibot-native-release.yml' "
        "and data.get('status')=='in_progress')\nraise SystemExit(0 if ok else 1)\n"
    )
    jq.chmod(0o700)
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\ncase "$*" in\n'
        '  "buildx imagetools inspect "*) printf "Digest: %s\\n    Platform: linux/amd64\\n" "$MANIFEST_DIGEST" ;;\n'
        '  "pull "*) exit 0 ;;\n'
        '  "image inspect "*) if [[ "$*" == *"{{.Os}}/{{.Architecture}}"* ]]; then echo linux/amd64; '
        'elif [[ "$*" == *"service"* ]]; then echo maxibot; else echo "$CANDIDATE_SHA"; fi ;;\n'
        '  *) exit 91 ;;\nesac\n'
    )
    docker.chmod(0o700)
    output, envfile, summary = (tmp_path / name for name in ("github-output", "github-env", "github-summary"))
    for path in (output, envfile, summary): path.touch()
    version = f"{build_image_sha}-{build_run_id}-{build_attempt}"
    env = {
        **os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CANDIDATE_SHA": sha, "RELEASE_OPERATION": "deploy", "GITHUB_SHA": sha, "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "push", "GITHUB_RUN_ID": build_run_id,
        "GITHUB_RUN_ATTEMPT": str(run_attempt), "GITHUB_REPOSITORY": "akhripunov/maxibot",
        "BUILD_RUN_ID": build_run_id, "BUILD_IMAGE_VERSION": version,
        "BUILD_RUN_JSON": str(tmp_path / "build-run.json"), "MAIN_SHA": sha,
        "APPROVED_DIGEST": digest, "MANIFEST_DIGEST": actual_digest,
        "GITHUB_OUTPUT": str(output), "GITHUB_ENV": str(envfile),
        "GITHUB_STEP_SUMMARY": str(summary), "GH_TOKEN": "test-only",
    }
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], cwd=ROOT, env=env, text=True, capture_output=True)
    return result, output.read_text()


@pytest.mark.parametrize(
    "current_attempt,build_attempt",
    [(1, 1), (2, 1), (2, 2)],
    ids=["ordinary-push", "failed-deploy-rerun-reuses-build-1", "full-rerun-uses-build-2"],
)
def test_build_selection_uses_the_successful_build_output_attempt(current_attempt, build_attempt, tmp_path):
    result, output = _run_build_selection(WORKFLOW.read_text(), tmp_path, run_attempt=current_attempt, build_attempt=build_attempt)
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"image_version={'a' * 40}-40597659419-{build_attempt}" in output


@pytest.mark.parametrize("wrong_sha,wrong_digest", [(True, False), (False, True)])
def test_build_selection_fails_closed_on_wrong_sha_or_digest(wrong_sha, wrong_digest, tmp_path):
    correct = "a" * 40
    result, _ = _run_build_selection(
        WORKFLOW.read_text(), tmp_path, run_attempt=2, build_attempt=1,
        build_sha=("b" * 40 if wrong_sha else correct),
        approved_digest=("sha256:" + "2" * 64 if wrong_digest else None),
        manifest_digest=("sha256:" + "1" * 64 if wrong_digest else None),
    )
    assert result.returncode != 0


def test_automatic_deploy_keeps_environment_serialization_and_single_apply():
    document = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    deploy = document["jobs"]["deploy"]
    assert deploy["environment"]["name"] == "production"
    assert document["concurrency"]["group"] == "maxibot-production-release"
    assert document["concurrency"]["cancel-in-progress"] == "false"
    assert deploy["needs"] == ["tests", "build"]
    assert "needs.build.result == 'success'" in deploy["if"]
    assert "inputs.operation == 'rollback'" in deploy["if"]
    assert not any("approval" in step.get("name", "").lower() for step in deploy["steps"])
    api_applies = [step for step in deploy["steps"] if 'bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles api' in step.get("run", "")]
    worker_applies = [step for step in deploy["steps"] if 'bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles worker' in step.get("run", "")]
    assert len(api_applies) == 1
    assert len(worker_applies) == 1
