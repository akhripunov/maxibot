import sys
import tempfile
import unittest
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
        self.calls = []
        self.claimed = False
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

    def dispatch(self, payload=None):
        return orchestrator.dispatch_prepare(payload or event(), self.api, repo_root=self.root,
                                             migration="0098_test", orchestrator_run_id="987")

    def test_successful_prepare_is_exactly_claimed_and_dispatches_preflight_only(self):
        receipt = self.dispatch()
        self.assertEqual(receipt["state"], "PREFLIGHT_DISPATCHED")
        call = next(c for c in self.api.calls if c[0] == "POST" and c[1].endswith("/dispatches"))
        self.assertEqual(call[2]["inputs"]["mode"], "preflight")
        self.assertEqual(call[2]["inputs"]["approve_production"], "false")
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
        self.dispatch()
        duplicate = self.dispatch()
        self.assertEqual(duplicate["state"], "PREFLIGHT_ALREADY_CLAIMED")
        self.assertEqual(sum(c[0] == "POST" and c[1].endswith("/dispatches") for c in self.api.calls), 1)

    def test_mutable_and_draft_release_are_rejected(self):
        for patch in ({"immutable": False}, {"draft": True}):
            api = FakeAPI()
            api.release_patch = patch
            with self.subTest(patch=patch), self.assertRaisesRegex(orchestrator.OrchestrationError, "immutable"):
                orchestrator.dispatch_prepare(event(), api, repo_root=self.root, migration="0098_test")

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
            "Preflight dispatch failed after durable claim; publication recovery required")
        dispatch_error.__cause__ = orchestrator.APIError(
            422, "Bearer another-secret", "actions/workflows/maxibot-deploy.yml/dispatches")
        detail = orchestrator.safe_failure_detail(dispatch_error)
        self.assertEqual(detail, "dispatch_after_claim_github_api_preflight_dispatch_http_422")
        self.assertNotIn("another-secret", detail)

    def test_unrecognized_orchestration_error_does_not_emit_untrusted_message(self):
        error = orchestrator.OrchestrationError("token=do-not-log")
        self.assertEqual(orchestrator.safe_failure_detail(error), "orchestration_guard_failed")

    def test_ready_preflight_dispatches_existing_task_scoped_deploy_once(self):
        ready = {"candidate_sha": SHA, "mode": "preflight", "preflight": "PASS",
                 "release_state": "READY_FOR_OWNER", "status": "PLANNED",
                 "production_changed": False, "task_authorization_id": "maxibot-task-2026-10-16-001",
                 "authorization_mode": "EXACT_SHA", "authorization_consumed": False,
                 "production_baseline_sha": "d" * 40,
                 "target_migration": "0098_test", "prepare_run_id": "123",
                 "prepare_run_attempt": "2", "github_run_id": "456", "github_run_attempt": "1"}
        receipt = orchestrator.dispatch_task_scoped_auto(
            ready, self.api, current_main_sha=SHA,
            validate_authorization=lambda result: {"authorization_id": result["task_authorization_id"]},
            preflight_run_id="456", preflight_run_attempt="1")
        self.assertEqual(receipt["state"], "TASK_SCOPED_AUTO_DISPATCHED")
        dispatch = next(call for call in self.api.calls if call[0] == "POST" and call[1].endswith("/dispatches"))
        self.assertEqual(dispatch[2], {"ref": "main", "inputs": {
            "candidate": SHA, "target_migration": "0098_test", "mode": "task-scoped-auto",
            "approve_production": "false", "approve_worker_bootstrap": "false",
            "prepare_run_id": "123", "prepare_run_attempt": "2", "orchestrator_run_id": "456",
            "task_authorization_id": "maxibot-task-2026-10-16-001"}})

    def test_task_scoped_dispatch_rejects_nonready_or_ambiguous_evidence(self):
        base = {"candidate_sha": SHA, "mode": "preflight", "preflight": "PASS",
                "release_state": "READY_FOR_OWNER", "status": "PLANNED", "production_changed": False,
                "authorization_mode": "EXACT_SHA", "authorization_consumed": False,
                "task_authorization_id": "maxibot-task-2026-10-16-001", "target_migration": "0098_test",
                "production_baseline_sha": "d" * 40,
                "prepare_run_id": "123", "prepare_run_attempt": "2", "github_run_id": "456",
                "github_run_attempt": "1"}
        for change in ({"preflight": "FAILED"}, {"release_state": "BLOCKED"},
                       {"production_changed": True}, {"task_authorization_id": ""},
                       {"mode": "production"}):
            api = FakeAPI()
            with self.subTest(change=change), self.assertRaises(orchestrator.OrchestrationError):
                orchestrator.dispatch_task_scoped_auto(
                    {**base, **change}, api, current_main_sha=SHA,
                    validate_authorization=lambda result: {"authorization_id": result["task_authorization_id"]},
                    preflight_run_id="456", preflight_run_attempt="1")
            self.assertFalse(any(call[0] == "POST" and call[1].endswith("/dispatches") for call in api.calls))

    def test_task_scoped_dispatch_stops_on_main_drift_or_invalid_scope(self):
        ready = {"candidate_sha": SHA, "mode": "preflight", "preflight": "PASS",
                 "release_state": "READY_FOR_OWNER", "status": "PLANNED", "production_changed": False,
                 "authorization_mode": "EXACT_SHA", "authorization_consumed": False,
                 "task_authorization_id": "maxibot-task-2026-10-16-001", "target_migration": "0098_test",
                 "production_baseline_sha": "d" * 40,
                 "prepare_run_id": "123", "prepare_run_attempt": "2", "github_run_id": "456",
                 "github_run_attempt": "1"}
        for current, validator in (("c" * 40, lambda _: {}), (SHA, lambda _: (_ for _ in ()).throw(
                orchestrator.OrchestrationError("scope or baseline mismatch")))):
            api = FakeAPI()
            with self.subTest(current=current), self.assertRaises(orchestrator.OrchestrationError):
                orchestrator.dispatch_task_scoped_auto(ready, api, current_main_sha=current,
                                                       validate_authorization=validator,
                                                       preflight_run_id="456", preflight_run_attempt="1")
            self.assertFalse(any(call[0] == "POST" and call[1].endswith("/dispatches") for call in api.calls))

    def test_two_ready_events_cannot_dispatch_twice_for_one_authorization(self):
        ready = {"candidate_sha": SHA, "mode": "preflight", "preflight": "PASS",
                 "release_state": "READY_FOR_OWNER", "status": "PLANNED", "production_changed": False,
                 "task_authorization_id": "maxibot-task-2026-10-16-001", "target_migration": "0098_test",
                 "authorization_mode": "EXACT_SHA", "authorization_consumed": False,
                 "production_baseline_sha": "d" * 40, "prepare_run_id": "123", "prepare_run_attempt": "2",
                 "github_run_id": "456", "github_run_attempt": "1"}
        validate = lambda result: {"authorization_id": result["task_authorization_id"]}
        first = orchestrator.dispatch_task_scoped_auto(
            ready, self.api, current_main_sha=SHA, validate_authorization=validate,
            preflight_run_id="456", preflight_run_attempt="1")
        second_ready = {**ready, "github_run_id": "457"}
        second = orchestrator.dispatch_task_scoped_auto(
            second_ready, self.api, current_main_sha=SHA, validate_authorization=validate,
            preflight_run_id="457", preflight_run_attempt="1")
        self.assertEqual(first["state"], "TASK_SCOPED_AUTO_DISPATCHED")
        self.assertEqual(second["state"], "TASK_SCOPED_AUTO_ALREADY_CLAIMED")
        self.assertEqual(sum(call[0] == "POST" and call[1].endswith("/dispatches") for call in self.api.calls), 1)

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

    def test_only_successful_trusted_exact_preflight_event_is_accepted(self):
        run = {"id": 456, "run_attempt": 1, "head_sha": SHA, "head_branch": "main",
               "status": "completed", "conclusion": "success", "event": "workflow_dispatch",
               "path": ".github/workflows/maxibot-deploy.yml",
               "display_title": f"MAXIBOT preflight {SHA} by owner"}
        payload = {"repository": {"full_name": "akhripunov/maxibot"}, "workflow_run": run}
        self.assertEqual(orchestrator._validate_ready_preflight_event(payload), run)
        for update in ({"conclusion": "failure"}, {"head_branch": "feature"},
                       {"path": ".github/workflows/maxibot-prepare.yml"},
                       {"display_title": f"MAXIBOT production {SHA} by owner"}):
            with self.subTest(update=update), self.assertRaises(orchestrator.OrchestrationError):
                orchestrator._validate_ready_preflight_event(
                    {"repository": {"full_name": "akhripunov/maxibot"}, "workflow_run": {**run, **update}})


if __name__ == "__main__":
    unittest.main()
