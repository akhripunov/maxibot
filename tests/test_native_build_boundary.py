"""Security boundaries of the MAXIBOT Native First workflow.

These tests do not attest to production readiness or hosted cache performance.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".github/workflows/maxibot-native-release.yml"


class NativeBuildBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TEMPLATE.is_file(), "Native CI/build candidate is missing")
        self.source = TEMPLATE.read_text()

    def test_native_candidate_ci_is_active_and_single_workflow(self):
        self.assertFalse((ROOT / ".github/native-release/ci-build.yml").exists())
        self.assertIn("codex/native-release-maxibot", self.source)
        self.assertIn("- main", self.source)
        self.assertIn("workflow_dispatch:", self.source)
        self.assertNotIn("workflow_run:", self.source)
        self.assertNotIn("workflow_call:", self.source)

    def test_candidate_sha_is_used_for_all_events(self):
        self.assertIn("github.event_name == 'pull_request' && github.event.pull_request.head.sha", self.source)
        self.assertEqual(self.source.count("ref: ${{ env.CANDIDATE_SHA }}"), 2)
        self.assertIn("tags: ghcr.io/akhripunov/maxibot-vps-api:${{ steps.version.outputs.value }}", self.source)
        self.assertNotIn("${{ inputs.candidate }}", self.source)

    def test_production_deploy_follows_successful_main_build_or_explicit_rollback(self):
        deploy = self.source.split("\n  deploy:\n", 1)[1]
        self.assertIn("github.event_name == 'push'", deploy)
        self.assertIn("github.ref == 'refs/heads/main'", deploy)
        self.assertIn("needs.tests.result == 'success'", deploy)
        self.assertIn("needs.build.result == 'success'", deploy)
        self.assertIn("github.event_name == 'workflow_dispatch'", deploy)
        self.assertIn("github.actor == github.repository_owner", deploy)
        self.assertIn("inputs.operation == 'rollback'", deploy)
        self.assertNotIn("pull_request", deploy)
        self.assertIn("environment:\n      name: production", deploy)
        self.assertIn("deployments: write", deploy)

    def test_ordinary_deploy_checks_runtime_without_running_full_backup_restore(self):
        deploy = self.source.split("\n  deploy:\n", 1)[1]
        baseline = deploy.split("- name: Verify exact main and read the current production baseline", 1)[1]
        baseline = baseline.split("- name: Verify exact GHCR image and Kamal configuration", 1)[0]
        self.assertNotIn("MAXIBOT_BACKUP_MAX_AGE_SECONDS", deploy)
        self.assertNotIn("pg_restore", deploy)
        self.assertNotIn("native_restore_probe", deploy)
        self.assertIn("PostgreSQL is not accepting connections", baseline)
        self.assertIn("Redis PING command failed", baseline)
        self.assertIn("Redis did not return PONG", baseline)
        self.assertIn("API and Worker source SHAs do not match", baseline)
        self.assertIn("PRODUCTION_BASELINE=FAIL:", baseline)

    def test_first_cutover_keeps_backup_restore_checks_as_a_separate_procedure(self):
        cutover = (ROOT / ".github/workflows/maxibot-first-kamal-cutover.yml").read_text()
        self.assertIn("pg_restore --list", cutover)
        self.assertIn("cutover_restore_probe", cutover)
        self.assertIn("--network none", cutover)

    def test_legacy_release_workflows_have_no_automatic_triggers(self):
        prepare = (ROOT / ".github/workflows/maxibot-prepare.yml").read_text()
        auto = (ROOT / ".github/workflows/maxibot-auto-preflight.yml").read_text()
        prepare_events = prepare.split("on:", 1)[1].split("permissions:", 1)[0]
        auto_events = auto.split("on:", 1)[1].split("permissions:", 1)[0]
        self.assertNotIn("push:", prepare_events)
        self.assertNotIn("workflow_run:", auto_events)
        self.assertIn("if: ${{ false }}", auto)

    def test_write_job_requires_owner_main_and_exact_candidate(self):
        self.assertIn("github.actor == github.repository_owner", self.source)
        self.assertIn("github.ref == 'refs/heads/main'", self.source)
        self.assertIn('^[0-9a-f]{40}$', self.source)
        self.assertIn('test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"', self.source)
        self.assertIn('test "$CURRENT_MAIN" = "$CANDIDATE_SHA"', self.source)

    def test_actions_are_immutable_and_use_official_build_tools(self):
        actions = re.findall(r"uses:\s+([^\s#]+)", self.source)
        self.assertGreaterEqual(len(actions), 4)
        for action in actions:
            self.assertRegex(action, r"^[\w./-]+@[0-9a-f]{40}$")
        for action in ('docker/setup-buildx-action@', 'docker/build-push-action@', 'docker/login-action@'):
            self.assertIn(action, self.source)
        self.assertNotIn('docker/metadata-action', self.source)

    def test_build_has_native_cache_sha_and_digest_outputs(self):
        for value in ('type=gha', 'linux/amd64', 'org.opencontainers.image.revision',
                      'steps.image.outputs.digest', 'packages: write', 'push: true',
                      'sbom: true', 'provenance: mode=min'):
            self.assertIn(value, self.source)
        self.assertNotIn('docker save', self.source)
        self.assertNotIn('docker load', self.source)
        self.assertNotIn('upload-artifact', self.source)
        self.assertNotIn('gh release create', self.source)
        self.assertIn('docker buildx imagetools inspect "$IMAGE"', self.source)

    def test_buildkit_attestations_have_no_production_secret_inputs(self):
        if not (ROOT / 'pyproject.toml').exists():
            return
        start = self.source.index('id: image')
        end = self.source.index('Record the immutable build output', start)
        build_step = self.source[start:end]
        self.assertIn('sbom: true', build_step)
        self.assertIn('provenance: mode=min', build_step)
        self.assertNotIn('secrets.', build_step)
        self.assertNotIn('password:', build_step)

    def test_no_dispatch_inputs_are_interpolated_into_shell(self):
        lines = self.source.splitlines()
        in_run = False
        for line in lines:
            if line.strip() and not line.startswith('          '):
                in_run = False
            if re.match(r'        run:', line):
                in_run = True
            if in_run:
                self.assertNotIn('${{ inputs.', line)

    def test_ci_build_cannot_reach_production(self):
        build_only = self.source.split('\n  deploy:\n', 1)[0]
        for forbidden in ('self-hosted', 'environment: production', 'PRODUCTION_SSH',
                          'docker compose up', 'release_prod.sh', 'release-gpoint.mjs --apply',
                          'secrets.SSH', 'secrets.LINEAR', 'cancel-in-progress: true'):
            self.assertNotIn(forbidden, build_only)
        self.assertIn('contents: read', build_only)
        self.assertIn('persist-credentials: false', build_only)

    def test_build_cannot_bypass_existing_safety_suite(self):
        if (ROOT / 'pyproject.toml').exists():
            self.assertIn('python -m pytest -q', self.source)
            self.assertNotIn('scripts/maxibot_impact_v3.py', self.source)
            self.assertNotIn('scripts/prepare_frozen_path_guard.py', self.source)
            self.assertIn('needs: tests', self.source)
            self.assertIn("MAX_RUN_POLLING: 'false'", self.source)
            self.assertIn("ADS_AUTOPILOT_ENABLED: 'false'", self.source)
            self.assertIn("ADS_WRITE_ACTIONS_ENABLED: 'false'", self.source)
        else:
            self.assertIn('pnpm test:release', self.source)
            self.assertIn('needs: [javascript, python]', self.source)
            self.assertIn('pnpm typecheck', self.source)

    def test_maxibot_native_ci_uses_full_suite_without_release_controller(self):
        if not (ROOT / 'pyproject.toml').exists():
            return
        self.assertIn('python -m pytest -q', self.source)
        self.assertNotIn('scripts/maxibot_impact_v3.py', self.source)
        self.assertNotIn('scripts/prepare_frozen_path_guard.py', self.source)

    def test_maxibot_native_deploy_is_digest_bound_and_keeps_single_poller(self):
        if not (ROOT / 'pyproject.toml').exists():
            return
        self.assertIn("environment:\n      name: production", self.source)
        self.assertIn("group: maxibot-production-release", self.source)
        self.assertIn("needs.build.outputs.image_digest", self.source)
        self.assertIn("|| inputs.image_digest }}", self.source)
        self.assertIn("BUILD_IMAGE_VERSION: ${{ github.event_name == 'push'", self.source)
        self.assertIn('actions/runs/$BUILD_RUN_ID', self.source)
        self.assertIn('.head_sha == $sha', self.source)
        self.assertIn('image_version="$CANDIDATE_SHA-$BUILD_RUN_ID-$build_attempt"', self.source)
        self.assertIn("bundle exec kamal config", self.source)
        self.assertIn('bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles api', self.source)
        self.assertIn('bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles worker', self.source)
        self.assertIn('bundle exec kamal app stop --roles worker', self.source)
        self.assertIn("MAX_RUN_POLLING", self.source)
        self.assertIn('old_db_id', self.source)
        self.assertIn('old_redis_id', self.source)
        self.assertNotIn('old_caddy_id', self.source)
        self.assertIn('old_uploads_volume', self.source)
        self.assertIn('EXPECTED_DB_ID', self.source)
        self.assertIn('EXPECTED_REDIS_ID', self.source)
        self.assertIn('assert not old["State"]["Running"]', self.source)
        self.assertIn("health/live", self.source)
        self.assertIn("worker_health_preflight.py", self.source)
        self.assertIn("-- alembic app/db/models.py docker-compose.vps.yml", self.source)
        self.assertIn("-- docs/frozen_paths.yaml scripts/frozen_path_guard.sh", self.source)
        self.assertNotIn("scripts/release_prod.sh", self.source)
        self.assertNotIn("scripts/run_release_workflow.py", self.source)
        deploy = self.source.split("\n  deploy:\n", 1)[1]
        self.assertIn("scripts/frozen_path_guard.sh --base", deploy)
        self.assertIn("FROZEN_PATH_REGRESSION=PASS", deploy)
        self.assertIn('gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq .object.sha', deploy)
        self.assertIn('docker buildx imagetools inspect "$IMAGE_TAG"', deploy)
        self.assertIn('test "$digest_readback" = "$approved_digest"', deploy)
        self.assertIn('bundle exec kamal config', deploy)
        self.assertIn('docker network inspect kamal', deploy)
        self.assertNotIn('maxibot-vps-frontend', deploy)
        self.assertIn('docker exec "$api" python', deploy)
        self.assertIn('MAXIBOT_KAMAL_DEPLOY_VERIFY=PASS', deploy)
        self.assertNotIn('docker compose', deploy)
        self.assertNotIn('TASK_SCOPED_AUTO', deploy)
        self.assertNotIn('claimed', deploy)
        self.assertNotIn('consumed', deploy)

    def test_published_digest_is_pulled_and_candidate_identity_verified(self):
        if not (ROOT / 'pyproject.toml').exists():
            return
        self.assertIn('docker buildx imagetools inspect "$IMAGE"', self.source)
        deploy = self.source.split("\n  deploy:\n", 1)[1]
        self.assertIn('docker pull "$IMAGE_REF"', deploy)
        self.assertIn("RepoDigests", deploy)
        self.assertIn('org.maxibot.release.sha', self.source)
        self.assertIn('linux/amd64', self.source)

    def test_patched_pip_bootstrap_precedes_dependency_install(self):
        bootstrap = 'python -m pip install --no-cache-dir pip==26.2.2'
        dependency = ('python -m pip install -r ' if (ROOT / 'pyproject.toml').exists()
                      else "python -m pip install './services")
        self.assertIn(bootstrap, self.source)
        self.assertLess(self.source.index(bootstrap), self.source.index(dependency))

    def test_gpoint_ui_build_does_not_compile_backend(self):
        if (ROOT / 'pyproject.toml').exists():
            return
        recipe = (ROOT / 'infra/production/Miniapp.Dockerfile').read_text()
        self.assertIn('pnpm --filter @gpoint/miniapp build', recipe)
        self.assertNotIn('pnpm build', recipe)
        self.assertNotRegex(recipe, r'COPY apps/(api|bot|worker) ')
        self.assertIn('FROM scratch', recipe)
        self.assertIn('options: [full, app, agata, gsales, miniapp]', self.source)


if __name__ == '__main__':
    unittest.main()
