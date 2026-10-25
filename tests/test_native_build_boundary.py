"""Security boundaries of the dormant Native First CI/build candidate.

These tests do not attest to production readiness or hosted cache performance.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".github/native-release/ci-build.yml"


class NativeBuildBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TEMPLATE.is_file(), "Native CI/build candidate is missing")
        self.source = TEMPLATE.read_text()

    def test_candidate_is_not_an_active_second_pipeline(self):
        self.assertFalse((ROOT / ".github/workflows/native-release.yml").exists())
        self.assertIn("workflow_dispatch:", self.source)
        self.assertNotIn("workflow_run:", self.source)
        self.assertNotIn("workflow_call:", self.source)

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
                      'steps.image.outputs.digest', 'packages: write', 'push: true'):
            self.assertIn(value, self.source)
        self.assertNotIn('docker save', self.source)
        self.assertNotIn('docker load', self.source)
        self.assertNotIn('upload-artifact', self.source)
        self.assertNotIn('gh release create', self.source)

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
        for forbidden in ('self-hosted', 'environment: production', 'PRODUCTION_SSH',
                          'docker compose up', 'release_prod.sh', 'release-gpoint.mjs --apply',
                          'secrets.SSH', 'secrets.LINEAR', 'cancel-in-progress: true'):
            self.assertNotIn(forbidden, self.source)
        self.assertIn('contents: read', self.source)
        self.assertIn('persist-credentials: false', self.source)

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

    def test_published_digest_is_pulled_and_candidate_identity_verified(self):
        if not (ROOT / 'pyproject.toml').exists():
            return
        self.assertIn('ghcr.io/akhripunov/maxibot-vps-api@$IMAGE_DIGEST', self.source)
        self.assertIn('docker pull "$IMAGE"', self.source)
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
