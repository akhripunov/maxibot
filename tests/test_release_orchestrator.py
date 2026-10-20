import sys
import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_orchestrator as orchestrator
import run_release_workflow
from validate_release_dispatch import ValidationError

SHA = "a" * 40


def event(**overrides):
    run = {"id": 123, "run_attempt": 2, "head_sha": SHA, "head_branch": "main",
           "status": "completed", "conclusion": "success", "event": "push",
           "path": ".github/workflows/maxibot-prepare.yml"}
    run.update(overrides)
    return {"repository": {"full_name": "akhripunov/maxibot"}, "workflow_run": run}


class FakeAPI:
    def __init__(self):
        self.token = "test-token"
        self.calls = []
        self.claimed = False
        self.claimed_sha = SHA
        self.release_patch = {}
        self.evidence_result = None

    def get(self, path):
        self.calls.append(("GET", path))
        if path == "git/ref/heads/main":
            return {"object": {"sha": SHA}}
        if path == "actions/runs/123":
            return event()["workflow_run"]
        if path.startswith("releases/tags/"):
            tag = path.rsplit("/", 1)[1]
            return {"tag_name": tag, "draft": False, "immutable": True, "assets": [{
                "id": 456, "name": "bundle.zip", "state": "uploaded", "size": 99,
                "digest": "sha256:" + "b" * 64,
            }], **self.release_patch}
        if path.endswith("/claimed") and "task-auth/" in path and self.claimed:
            return {"object": {"type": "commit", "sha": self.claimed_sha}}
        if path.startswith("git/ref/tags/"):
            return {"object": {"type": "commit", "sha": SHA}}
        if path.startswith("git/ref/tags/maxibot-orchestrator/") and self.claimed:
            return {"object": {"type": "commit", "sha": SHA}}
        if "actions/workflows/maxibot-deploy.yml/runs" in path:
            return {"workflow_runs": []}
        raise orchestrator.APIError(404)

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        if path == "git/refs":
            if self.claimed:
                raise orchestrator.APIError(422)
            self.claimed = True
            self.claimed_sha = body["sha"]
            return {"ref": body["ref"]}
        if path.endswith("/dispatches"):
            return {}
        raise AssertionError(path)


class ReleaseOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = FakeAPI()
        env_patch = patch.dict(os.environ, {"GITHUB_ACTOR": "codex-test"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        def resolve(_repository, tag, _artifact_type, _layer_type, _fetch):
            import re
            match = re.fullmatch(r"prepare-([0-9a-f]{40})-([1-9][0-9]*)-([1-9][0-9]*)", tag)
            sha, run_id, attempt = match.groups()
            receipt = {"schema": "MAXIBOT_PREPARE_RECEIPT_V2", "candidate_sha": sha,
                "github_run_id": run_id, "github_run_attempt": attempt, "status": "READY",
                "tests": "success", "frozen_path_guard": "success", "linux_amd64_image": "success",
                "ghcr_image_publish": "success", "private_docker_rehearsal": "PASS",
                "image": {"repository": "ghcr.io/akhripunov/maxibot-vps-api", "tag": f"prepare-{sha}-{run_id}-{attempt}",
                    "digest": "sha256:" + "c" * 64, "config_digest": "sha256:" + "d" * 64,
                    "platform": "linux/amd64", "labels": {
                        "org.opencontainers.image.revision": sha,
                        "org.opencontainers.image.source-sha": sha,
                        "org.maxibot.release.sha": sha}}}
            return "sha256:" + "e" * 64, json.dumps(receipt).encode()
        patcher = patch.object(orchestrator, "resolve_oci_artifact", resolve)
        patcher.start()
        self.addCleanup(patcher.stop)

    def dispatch(self, payload=None):
        return orchestrator.dispatch_prepare(payload or event(), self.api, repo_root=self.root,
                                             migration="0098_test", orchestrator_run_id="987")

    def test_controller_checkout_fetches_full_history_for_authorization_ancestry(self):
        workflow = (ROOT / ".github/workflows/maxibot-auto-preflight.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^\s+fetch-depth:\s+0\s*$")

    def test_prepare_without_full_cycle_authorization_does_not_dispatch_preflight_or_production(self):
        receipt = self.dispatch()
        self.assertEqual(receipt["state"], "OWNER_EXACT_SHA_APPROVAL_REQUIRED")
        self.assertFalse(any(c[0] == "POST" for c in self.api.calls))

    def test_prepare_with_full_cycle_authorization_dispatches_existing_task_scoped_deploy(self):
        authorization_id = "maxibot-task-2026-10-16-001"
        with patch.object(orchestrator, "_find_task_authorization", return_value={
                "authorization_id": authorization_id, "ambiguous": "false"}):
            receipt = self.dispatch()
        self.assertEqual(receipt["state"], "TASK_SCOPED_AUTO_DISPATCHED")
        call = next(c for c in self.api.calls if c[0] == "POST" and c[1].endswith("/dispatches"))
        self.assertEqual(call[2]["inputs"]["mode"], "task-scoped-auto")
        self.assertEqual(call[2]["inputs"]["task_authorization_id"], authorization_id)
        self.assertEqual(call[2]["inputs"]["prepare_run_id"], "123")
        self.assertEqual(call[2]["inputs"]["prepare_run_attempt"], "2")
        self.assertEqual(call[2]["inputs"]["orchestrator_run_id"], "987")

    def test_failed_or_untrusted_prepare_never_dispatches(self):
        for overrides in ({"conclusion": "failure"}, {"head_branch": "feature"},
                          {"head_sha": "c" * 40}, {"status": "in_progress"},
                          {"path": ".github/workflows/other.yml"}):
            api = FakeAPI()
            with self.subTest(overrides=overrides), self.assertRaises(orchestrator.OrchestrationError):
                orchestrator.dispatch_prepare(event(**overrides), api, repo_root=self.root, migration="0098_test")
            self.assertFalse(any(c[0] == "POST" and c[1].endswith("/dispatches") for c in api.calls))

    def test_main_drift_blocks_before_claim_or_dispatch(self):
        self.api.get = lambda path: ({"object": {"sha": "c" * 40}} if path == "git/ref/heads/main"
                                     else FakeAPI.get(self.api, path))
        with self.assertRaisesRegex(orchestrator.OrchestrationError, "current main"):
            self.dispatch()
        self.assertFalse(any(c[0] == "POST" for c in self.api.calls))

    def test_duplicate_event_is_a_noop_and_does_not_dispatch_twice(self):
        authorization_id = "maxibot-task-2026-10-16-001"
        with patch.object(orchestrator, "_find_task_authorization", return_value={
                "authorization_id": authorization_id, "ambiguous": "false"}):
            self.dispatch()
            duplicate = self.dispatch()
        self.assertEqual(duplicate["state"], "TASK_SCOPED_AUTO_ALREADY_CLAIMED")
        self.assertEqual(sum(c[0] == "POST" and c[1].endswith("/dispatches") for c in self.api.calls), 1)

    def test_existing_authorization_claim_for_different_sha_blocks(self):
        authorization_id = "maxibot-task-2026-10-16-001"
        self.api.claimed = True
        self.api.claimed_sha = "c" * 40
        with patch.object(orchestrator, "_find_task_authorization", return_value={
                "authorization_id": authorization_id, "ambiguous": "false"}):
            with self.assertRaisesRegex(orchestrator.OrchestrationError, "invalid"):
                self.dispatch()
        self.assertFalse(any(c[0] == "POST" and c[1].endswith("/dispatches") for c in self.api.calls))

    def test_invalid_oci_prepare_receipt_is_rejected(self):
        with patch.object(orchestrator, "resolve_oci_artifact", side_effect=orchestrator.ArtifactError("invalid")):
            with self.assertRaisesRegex(orchestrator.OrchestrationError, "OCI receipt provenance"):
                self.dispatch()

    def test_only_single_alembic_head_is_accepted(self):
        versions = self.root / "alembic" / "versions"
        versions.mkdir(parents=True)
        (versions / "a.py").write_text("revision='a'\ndown_revision=None\n")
        (versions / "b.py").write_text("revision='b'\ndown_revision='a'\n")
        self.assertEqual(orchestrator.alembic_heads(self.root), ["b"])

    def test_cost_telemetry_uses_runner_seconds_not_billing_minutes(self):
        jobs = [
            {"runner_name": "GitHub Actions 10", "started_at": "2026-10-05T00:00:00Z", "completed_at": "2026-10-05T00:00:12Z"},
            {"runner_name": "maxibot-owner-mac", "started_at": "2026-10-05T00:00:00Z", "completed_at": "2026-10-05T00:00:30Z"},
        ]
        self.assertEqual(orchestrator.runner_telemetry(jobs), {
            "hosted_job_count": 1, "hosted_runner_seconds": 12,
            "self_hosted_job_count": 1, "self_hosted_runner_seconds": 30})

    def test_failure_diagnostics_identify_safe_reason_without_api_body(self):
        api_error = orchestrator.APIError(503, "Bearer secret-token response body", "git/refs")
        detail = orchestrator.safe_failure_detail(api_error)
        self.assertEqual(detail, "github_api_claim_create_http_503")
        self.assertNotIn("secret-token", detail)

        dispatch_error = orchestrator.OrchestrationError(
            "Task-scoped Deploy dispatch failed after durable claim; reconcile before retry")
        dispatch_error.__cause__ = orchestrator.APIError(
            422, "Bearer another-secret", "actions/workflows/maxibot-deploy.yml/dispatches")
        detail = orchestrator.safe_failure_detail(dispatch_error)
        self.assertEqual(detail, "dispatch_after_claim_github_api_task_auto_dispatch_http_422")
        self.assertNotIn("another-secret", detail)

    def test_unrecognized_orchestration_error_does_not_emit_untrusted_message(self):
        error = orchestrator.OrchestrationError("token=do-not-log")
        self.assertEqual(orchestrator.safe_failure_detail(error), "orchestration_guard_failed")

    def test_preflight_authorization_requires_no_claim_but_task_deploy_requires_exact_claim(self):
        authorization_id = "maxibot-task-2026-10-16-001"
        record = {
            "schema_version": 3, "project": "maxibot",
            "authorization_mode": "TASK_SCOPED_AUTO", "authorization_id": authorization_id,
            "owner_instruction_scope": "FULL_PRODUCTION_CYCLE",
            "source_reference": "linear:ME-31/owner-instruction",
            "production_baseline_sha": "d" * 40, "task": "Bounded product task",
            "allowed_paths": ["app/bot/menu.py"], "acceptance_criteria": ["menu works"],
            "exclusions": ["database", "migrations", "security", "secrets", "payments",
                           "ads", "paid_traffic", "release_infrastructure"],
            "state": "ACTIVE", "revoked": False, "consumed": False,
        }
        claim_ref = "git/ref/tags/maxibot-task-auth/" + authorization_id + "/claimed"
        absent = ValidationError("GitHub release provenance request failed (HTTP 404)")

        def lookup_without_claim(path, _token):
            if path == "git/ref/heads/main":
                return {"object": {"sha": SHA}}
            if path == claim_ref:
                raise absent
            if "/revoked" in path or "/consumed" in path:
                raise ValidationError("GitHub release provenance request failed (HTTP 404)")
            raise AssertionError(path)

        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / "auth.json"
            auth_file.write_text(__import__("json").dumps(record))
            with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}), \
                    patch.object(run_release_workflow, "record_path", return_value=str(auth_file)), \
                    patch.object(run_release_workflow, "candidate_scope_diff", return_value=["app/bot/menu.py"]), \
                    patch("validate_release_dispatch.github_get", side_effect=lookup_without_claim):
                ready = run_release_workflow.verify_task_authorization(
                    authorization_id, SHA, "d" * 40, require_claim=False)
                self.assertEqual(ready["authorization_id"], authorization_id)
                with self.assertRaisesRegex(ValueError, "no exact-candidate atomic Deploy claim"):
                    run_release_workflow.verify_task_authorization(
                        authorization_id, SHA, "d" * 40, require_claim=True)

                def lookup_with_claim(path, _token):
                    if path == claim_ref:
                        return {"object": {"sha": SHA}}
                    return lookup_without_claim(path, _token)

                with patch("validate_release_dispatch.github_get", side_effect=lookup_with_claim):
                    deployed = run_release_workflow.verify_task_authorization(
                        authorization_id, SHA, "d" * 40, require_claim=True)
                self.assertEqual(deployed["authorization_id"], authorization_id)

                def lookup_with_foreign_claim(path, _token):
                    if path == claim_ref:
                        return {"object": {"sha": "b" * 40}}
                    return lookup_without_claim(path, _token)

                with patch("validate_release_dispatch.github_get", side_effect=lookup_with_foreign_claim):
                    with self.assertRaisesRegex(ValueError, "no exact-candidate atomic Deploy claim"):
                        run_release_workflow.verify_task_authorization(
                            authorization_id, SHA, "d" * 40, require_claim=True)
