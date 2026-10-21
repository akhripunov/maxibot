import sys
import json
import tempfile
import unittest
import os
import re
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_orchestrator as orchestrator
import run_release_workflow
from validate_release_dispatch import ValidationError
from release_authorization import owner_task_commitment_sha256

SHA = "a" * 40

def task_record(authorization_id):
    record = {
        "schema_version": 3, "project": "maxibot", "environment": "production",
        "authorization_mode": "TASK_SCOPED_AUTO", "authorization_id": authorization_id,
        "source_instruction_identifier": "OWNER_TASK_MAXIBOT_ME45_CONTROLLER",
        "owner_task_reference": "Direct OWNER authorization for this bounded MAXIBOT release task.",
        "owner_instruction_scope": "FULL_PRODUCTION_CYCLE",
        "candidate_selection_rule": "EXACT_CURRENT_MAIN_WITHIN_AUTHORIZED_SCOPE",
        "production_baseline_sha": "d" * 40, "task": "Bounded release controller task",
        "allowed_paths": ["scripts/release_orchestrator.py"],
        "allowed_operations": ["integrate", "coalesce", "prepare", "preflight", "deploy", "verify", "rollback", "publish_evidence"],
        "preauthorized_commits": [], "preauthorized_paths": [],
        "acceptance_criteria": ["All release gates pass."],
        "exclusions": ["database", "migrations", "security", "secrets", "payments", "ads", "paid_traffic"],
        "risk_contracts": {}, "state": "ACTIVE", "revoked": False, "consumed": False,
    }
    record["owner_task_commitment_sha256"] = owner_task_commitment_sha256(record)
    return record


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
        self.refs = {}
        self.workflow_runs = []
        self.jobs_by_attempt = {}
        self.logs_by_job = {}
        self.job_total_override = None

    def get(self, path):
        self.calls.append(("GET", path))
        if path == "git/ref/heads/main":
            return {"object": {"sha": SHA}}
        if path == "actions/runs/123":
            return event()["workflow_run"]
        job_match = re.fullmatch(r"actions/runs/([0-9]+)/attempts/([0-9]+)/jobs\?per_page=100", path)
        if job_match:
            jobs = self.jobs_by_attempt.get("/".join(job_match.groups()), [])
            return {"total_count": self.job_total_override if self.job_total_override is not None else len(jobs),
                    "jobs": jobs}
        if path.startswith("releases/tags/"):
            tag = path.rsplit("/", 1)[1]
            return {"tag_name": tag, "draft": False, "immutable": True, "assets": [{
                "id": 456, "name": "bundle.zip", "state": "uploaded", "size": 99,
                "digest": "sha256:" + "b" * 64,
            }], **self.release_patch}
        if path.endswith("/claimed") and "task-auth/" in path and self.claimed:
            return {"object": {"type": "commit", "sha": self.claimed_sha}}
        if path.startswith("git/matching-refs/tags/"):
            prefix = "refs/tags/" + path.removeprefix("git/matching-refs/tags/")
            return [{"ref": ref, "object": {"type": "commit", "sha": sha}}
                    for ref, sha in self.refs.items() if ref.startswith(prefix)]
        if path.startswith("git/ref/tags/"):
            ref = "refs/tags/" + path.removeprefix("git/ref/tags/")
            if ref in self.refs:
                return {"object": {"type": "commit", "sha": self.refs[ref]}}
        if path.startswith("git/ref/tags/"):
            return {"object": {"type": "commit", "sha": SHA}}
        if path.startswith("git/ref/tags/maxibot-orchestrator/") and self.claimed:
            return {"object": {"type": "commit", "sha": SHA}}
        if "actions/workflows/maxibot-deploy.yml/runs" in path:
            return {"total_count": len(self.workflow_runs), "workflow_runs": self.workflow_runs}
        raise orchestrator.APIError(404)

    def get_job_log_archive(self, job_id):
        return self.logs_by_job[job_id]

    def add_publisher(self, run_id, attempt, job_id, step_name, digest, conclusion="success"):
        self.jobs_by_attempt[f"{run_id}/{attempt}"] = [{"id": int(job_id), "run_id": int(run_id),
            "steps": [{"name": step_name, "conclusion": conclusion}]}]
        prefix = ("MAXIBOT_GHCR_PREPARE_RECEIPT=PASS DIGEST=" if "Prepare" in step_name
                  else "MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST=")
        self.logs_by_job[str(job_id)] = (
            "2026-10-20T17:19:54.2975796Z Run python3 scripts/publish.py\n"
            f"2026-10-20T17:19:54.2975796Z {prefix}{digest}\n")

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        if path == "git/refs":
            if body["ref"].endswith("/claimed") and self.claimed:
                raise orchestrator.APIError(422)
            if body["ref"] in self.refs:
                raise orchestrator.APIError(422)
            self.refs[body["ref"]] = body["sha"]
            if body["ref"].endswith("/claimed"):
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
        self.api.add_publisher("123", "2", "124", orchestrator.PREPARE_PUBLISH_STEP,
                               "sha256:" + "e" * 64)
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
        authorization_id = "maxibot-task-2026-10-16-001"
        record_dir = self.root / ".release-authorizations"
        record_dir.mkdir(exist_ok=True)
        (record_dir / f"{authorization_id}.json").write_text(json.dumps(task_record(authorization_id)))
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

    def test_duplicate_event_with_unreconciled_marker_blocks_without_redispatch(self):
        authorization_id = "maxibot-task-2026-10-16-001"
        with patch.object(orchestrator, "_find_task_authorization", return_value={
                "authorization_id": authorization_id, "ambiguous": "false"}):
            self.dispatch()
            with self.assertRaisesRegex(orchestrator.OrchestrationError, "reconciliation required"):
                self.dispatch()
        self.assertEqual(sum(c[0] == "POST" and c[1].endswith("/dispatches") for c in self.api.calls), 1)

    def test_duplicate_event_is_noop_only_after_exact_immutable_evidence_reconciliation(self):
        authorization_id = "maxibot-task-2026-10-16-001"
        with patch.object(orchestrator, "_find_task_authorization", return_value={
                "authorization_id": authorization_id, "ambiguous": "false"}):
            first = self.dispatch()
            self.assertEqual(first["state"], "TASK_SCOPED_AUTO_DISPATCHED")
            self.api.workflow_runs = [{"id": 789, "run_attempt": 1, "head_sha": SHA,
                "path": ".github/workflows/maxibot-deploy.yml", "event": "workflow_dispatch",
                "status": "completed", "conclusion": "failure"}]
            self.api.add_publisher("789", "1", "790", orchestrator.EVIDENCE_PUBLISH_STEP,
                                   "sha256:" + "e" * 64)
            exact = {"schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1", "candidate_sha": SHA,
                "github_run_id": "789", "github_run_attempt": "1", "prepare_run_id": "123",
                "prepare_run_attempt": "2", "task_authorization_id": authorization_id,
                "mode": "task-scoped-auto", "status": "FAILED_PRE_SWITCH",
                "release_state": "FAILED_PRE_SWITCH", "production_changed": False,
                "authorization_consumed": False, "production_baseline_sha": "d" * 40}
            original = orchestrator.resolve_oci_artifact

            def resolve(repository, tag, artifact_type, layer_type, fetch):
                if tag.startswith("evidence-"):
                    return "sha256:" + "e" * 64, json.dumps(exact).encode()
                return original(repository, tag, artifact_type, layer_type, fetch)

            with patch.object(orchestrator, "resolve_oci_artifact", side_effect=resolve):
                duplicate = self.dispatch()
        self.assertEqual(duplicate["state"], "TASK_SCOPED_AUTO_EVENT_ALREADY_DISPATCHED")
        self.assertEqual(sum(c[0] == "POST" and c[1].endswith("/dispatches") for c in self.api.calls), 1)

    def test_github_get_retries_transient_transport_failure_with_bounded_read_retry(self):
        calls = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return b'{"object":{"sha":"' + SHA.encode() + b'"}}'

        def opener(request, timeout):
            calls.append((request.method, timeout))
            if len(calls) == 1:
                raise urllib.error.URLError(TimeoutError("temporary"))
            return Response()

        api = orchestrator.GitHubAPI("test-token", opener=opener)
        with patch.object(orchestrator, "NETWORK_BUDGET",
                          orchestrator.RetryBudget(seconds=10)), \
                patch.object(orchestrator.time, "sleep", lambda _seconds: None), \
                patch.object(orchestrator, "retry_jitter", lambda _low, _high: 0):
            result = api.get("git/ref/heads/main")
        self.assertEqual(result["object"]["sha"], SHA)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(method == "GET" for method, _ in calls))

    def test_github_post_is_never_retried_after_transport_error(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.method)
            raise urllib.error.URLError(TimeoutError("temporary"))

        api = orchestrator.GitHubAPI("test-token", opener=opener)
        with self.assertRaises(orchestrator.OrchestrationError):
            api.post("actions/workflows/maxibot-deploy.yml/dispatches", {"ref": "main"})
        self.assertEqual(calls, ["POST"])

    def test_controller_reports_invalid_event_without_name_error_in_exception_handler(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": "/no/such/event.json"}), \
                patch("sys.stderr"):
            self.assertEqual(orchestrator.main(), 1)

    def test_prior_dispatch_requires_exact_failed_pre_switch_evidence_and_baseline(self):
        marker = {"ref": "maxibot-task-auth/auth-id/dispatches/prepare-123-2", "sha": SHA}
        runs = [{"id": 991, "run_attempt": 1, "head_sha": SHA,
                 "path": ".github/workflows/maxibot-deploy.yml", "event": "workflow_dispatch",
                 "status": "completed", "conclusion": "failure"}]
        evidence = {"schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1", "candidate_sha": SHA,
                    "github_run_id": "991", "github_run_attempt": "1",
                    "prepare_run_id": "123", "prepare_run_attempt": "2",
                    "task_authorization_id": "auth-id", "mode": "task-scoped-auto",
                    "status": "FAILED_PRE_SWITCH", "release_state": "FAILED_PRE_SWITCH",
                    "production_changed": False, "authorization_consumed": False,
                    "production_baseline_sha": "d" * 40}
        with patch.object(orchestrator, "resolve_oci_artifact",
                          return_value=("sha256:" + "e" * 64, json.dumps(evidence).encode())):
            self.api.add_publisher("991", "1", "992", orchestrator.EVIDENCE_PUBLISH_STEP,
                                   "sha256:" + "e" * 64)
            orchestrator._prove_safe_prior_dispatch(self.api, marker, runs, "auth-id", "d" * 40)
            with self.assertRaisesRegex(orchestrator.OrchestrationError, "not proven safe"):
                orchestrator._prove_safe_prior_dispatch(self.api, marker, runs, "auth-id", "f" * 40)
            self.api.add_publisher("991", "1", "992", orchestrator.EVIDENCE_PUBLISH_STEP,
                                   "sha256:" + "f" * 64)
            with self.assertRaisesRegex(orchestrator.OrchestrationError, "unknown"):
                orchestrator._prove_safe_prior_dispatch(self.api, marker, runs, "auth-id", "d" * 40)
        with self.assertRaisesRegex(orchestrator.OrchestrationError, "unknown"):
            orchestrator._prove_safe_prior_dispatch(self.api, marker, [], "auth-id", "d" * 40)

    def _set_predeploy_failure(self, *, candidate=SHA, prepare_id="123", prepare_attempt="2",
                                authorization_id="auth-id", deploy_steps=None,
                                validate_steps=None, total_count=3, duplicate_env=False,
                                mode="task-scoped-auto", job_run_id=None, missing_deploy_steps=False):
        run_id, attempt = "991", "1"
        validate_steps = validate_steps or [
            {"name": "Set up job", "status": "completed", "conclusion": "success"},
            {"name": "Initialize shared retry budget", "status": "completed", "conclusion": "success"},
            {"name": "Checkout trusted main workflow source", "status": "completed", "conclusion": "success"},
            {"name": "Verify approval, current main and latest successful Prepare", "status": "completed", "conclusion": "failure"},
        ]
        jobs = [
            {"id": 992, "run_id": job_run_id if job_run_id is not None else int(run_id), "run_attempt": int(attempt), "head_sha": candidate, "name": "validate",
             "status": "completed", "conclusion": "failure", "steps": validate_steps},
            {"id": 993, "run_id": int(run_id), "run_attempt": int(attempt), "head_sha": candidate, "name": "deploy",
             "status": "completed", "conclusion": "skipped", "steps": None if missing_deploy_steps else (deploy_steps or [])},
            {"id": 994, "run_id": int(run_id), "run_attempt": int(attempt), "head_sha": candidate, "name": "publish",
             "status": "completed", "conclusion": "failure", "steps": []},
        ]
        self.api.jobs_by_attempt[f"{run_id}/{attempt}"] = jobs
        self.api.job_total_override = total_count
        fields = {"CANDIDATE_SHA": candidate, "RELEASE_MODE": mode,
                  "PREPARE_RUN_ID": prepare_id, "PREPARE_RUN_ATTEMPT": prepare_attempt,
                  "TASK_AUTHORIZATION_ID": authorization_id, "APPROVE_PRODUCTION": "false",
                  "APPROVE_WORKER_BOOTSTRAP": "false"}
        env = "\n".join(f"2026-10-21T04:01:38.8820000Z   {key}: {value}" for key, value in fields.items())
        section = ("2026-10-21T04:01:38.8820000Z ##[group]Run python3 scripts/validate_release_dispatch.py\n"
                   "2026-10-21T04:01:38.8820000Z   python3 scripts/validate_release_dispatch.py\n"
                   "2026-10-21T04:01:38.8820000Z env:\n" + env + "\n"
                   "2026-10-21T04:01:38.8820000Z ##[endgroup]\n"
                   "2026-10-21T04:01:38.8820000Z RELEASE_PROVENANCE=BLOCKED REASON=validation failed\n")
        if duplicate_env:
            section += section
        self.api.logs_by_job["992"] = section
        return {"id": int(run_id), "run_attempt": int(attempt), "head_sha": candidate,
                "head_branch": "main", "path": ".github/workflows/maxibot-deploy.yml",
                "event": "workflow_dispatch", "status": "completed", "conclusion": "failure"}

    def test_validate_failure_with_skipped_empty_deploy_is_safe_to_retry(self):
        authorization_id = "auth-id"
        prior_sha = "c" * 40
        marker = {"ref": "maxibot-task-auth/auth-id/dispatches/prepare-123-2", "sha": prior_sha}
        run = self._set_predeploy_failure(candidate=prior_sha)
        with patch.object(orchestrator, "resolve_oci_artifact",
                          side_effect=orchestrator.ArtifactError("no evidence is published before deploy")):
            orchestrator._prove_safe_prior_dispatch(
                self.api, marker, [run], authorization_id, "d" * 40)

    def test_predeploy_failure_proof_allows_only_known_post_checkout_completion_steps(self):
        marker = {"ref": "maxibot-task-auth/auth-id/dispatches/prepare-123-2", "sha": "c" * 40}
        steps = [
            {"name": "Set up job", "status": "completed", "conclusion": "success"},
            {"name": "Initialize shared retry budget", "status": "completed", "conclusion": "success"},
            {"name": "Checkout trusted main workflow source", "status": "completed", "conclusion": "success"},
            {"name": orchestrator.DEPLOY_VALIDATE_STEP, "status": "completed", "conclusion": "failure"},
            {"name": "Post Checkout trusted main workflow source", "status": "completed", "conclusion": "success"},
            {"name": "Complete job", "status": "completed", "conclusion": "success"},
        ]
        run = self._set_predeploy_failure(candidate="c" * 40, validate_steps=steps)
        orchestrator._prove_safe_prior_dispatch(self.api, marker, [run], "auth-id", "d" * 40)

    def test_predeploy_failure_proof_uses_first_group_end_and_requires_timestamp(self):
        marker = {"ref": "maxibot-task-auth/auth-id/dispatches/prepare-123-2", "sha": "c" * 40}
        run = self._set_predeploy_failure(candidate="c" * 40)
        self.api.logs_by_job["992"] += (
            "2026-10-21T04:01:40.0000000Z ##[group]Post Checkout\n"
            "2026-10-21T04:01:40.0000000Z ##[endgroup]\n"
        )
        orchestrator._prove_safe_prior_dispatch(self.api, marker, [run], "auth-id", "d" * 40)

        self.api.logs_by_job["992"] = self.api.logs_by_job["992"].replace(
            "2026-10-21T04:01:38.8820000Z   CANDIDATE_SHA:", "CANDIDATE_SHA:")
        with self.assertRaises(orchestrator.OrchestrationError):
            orchestrator._prove_safe_prior_dispatch(self.api, marker, [run], "auth-id", "d" * 40)

    def test_predeploy_failure_proof_rejects_wrong_job_identity_and_unknown_sibling_run(self):
        marker = {"ref": "maxibot-task-auth/auth-id/dispatches/prepare-123-2", "sha": "c" * 40}
        run = self._set_predeploy_failure(candidate="c" * 40)
        self.api.jobs_by_attempt["991/1"][0]["head_sha"] = "d" * 40
        with self.assertRaises(orchestrator.OrchestrationError):
            orchestrator._prove_safe_prior_dispatch(self.api, marker, [run], "auth-id", "d" * 40)

        run = self._set_predeploy_failure(candidate="c" * 40)
        unknown = dict(run, id=992, run_attempt=2)
        self.api.jobs_by_attempt["991/2"] = []
        with self.assertRaises(orchestrator.OrchestrationError):
            orchestrator._prove_safe_prior_dispatch(self.api, marker, [run, unknown], "auth-id", "d" * 40)

    def test_predeploy_failure_proof_rejects_missing_or_running_validate_step(self):
        marker = {"ref": "maxibot-task-auth/auth-id/dispatches/prepare-123-2", "sha": "c" * 40}
        steps = [
            {"name": "Set up job", "status": "completed", "conclusion": "success"},
            {"name": "Initialize shared retry budget", "status": "completed", "conclusion": "success"},
            {"name": "Checkout trusted main workflow source", "status": "completed", "conclusion": "success"},
            {"name": orchestrator.DEPLOY_VALIDATE_STEP, "status": "in_progress", "conclusion": None},
        ]
        run = self._set_predeploy_failure(candidate="c" * 40, validate_steps=steps)
        with self.assertRaises(orchestrator.OrchestrationError):
            orchestrator._prove_safe_prior_dispatch(self.api, marker, [run], "auth-id", "d" * 40)

    def test_predeploy_failure_proof_rejects_partial_or_ambiguous_execution(self):
        marker = {"ref": "maxibot-task-auth/auth-id/dispatches/prepare-123-2", "sha": "c" * 40}
        cases = [
            {"total_count": 4},
            {"deploy_steps": [{"name": "Run production apply", "conclusion": "success"}]},
            {"authorization_id": "different-auth"},
            {"prepare_id": "999"},
            {"prepare_attempt": "3"},
            {"mode": "manual"},
            {"job_run_id": 777},
            {"missing_deploy_steps": True},
            {"duplicate_env": True},
            {"validate_steps": [{"name": "Checkout trusted main workflow source", "conclusion": "success"}]},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                run = self._set_predeploy_failure(candidate="c" * 40, **overrides)
                with patch.object(orchestrator, "resolve_oci_artifact",
                                  side_effect=orchestrator.ArtifactError("no evidence")):
                    with self.assertRaises(orchestrator.OrchestrationError):
                        orchestrator._prove_safe_prior_dispatch(
                            self.api, marker, [run], "auth-id", "d" * 40)

    def test_replacement_candidate_dispatches_only_after_proven_safe_prior_failure(self):
        authorization_id = "maxibot-task-2026-10-16-001"
        prior_sha = "c" * 40
        prior_marker = orchestrator._task_dispatch_ref(authorization_id, "456", "1")
        self.api.claimed = True
        self.api.claimed_sha = prior_sha
        self.api.refs[prior_marker] = prior_sha
        self.api.workflow_runs = [{"id": 789, "run_attempt": 1, "head_sha": prior_sha,
            "path": ".github/workflows/maxibot-deploy.yml", "event": "workflow_dispatch",
            "status": "completed", "conclusion": "failure"}]
        self.api.add_publisher("789", "1", "790", orchestrator.EVIDENCE_PUBLISH_STEP,
                               "sha256:" + "e" * 64)
        failure_result = {"schema": "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1", "candidate_sha": prior_sha,
            "github_run_id": "789", "github_run_attempt": "1", "prepare_run_id": "456",
            "prepare_run_attempt": "1", "task_authorization_id": authorization_id,
            "mode": "task-scoped-auto", "status": "FAILED_PRE_SWITCH",
            "release_state": "FAILED_PRE_SWITCH", "production_changed": False,
            "authorization_consumed": False, "production_baseline_sha": "d" * 40}
        original_resolve = orchestrator.resolve_oci_artifact

        def resolve(repository, tag, artifact_type, layer_type, fetch):
            if tag.startswith("evidence-"):
                return "sha256:" + "e" * 64, json.dumps(failure_result).encode()
            return original_resolve(repository, tag, artifact_type, layer_type, fetch)

        with patch.object(orchestrator, "_find_task_authorization", return_value={
                "authorization_id": authorization_id, "ambiguous": "false"}), \
                patch.object(orchestrator, "validate_claim_candidate"), \
                patch.object(orchestrator, "resolve_oci_artifact", side_effect=resolve):
            result = self.dispatch()
        self.assertEqual(result["state"], "TASK_SCOPED_AUTO_DISPATCHED")
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

    def test_prepare_receipt_tag_must_match_successful_publisher_log_digest(self):
        with patch.object(orchestrator, "_find_task_authorization", return_value={
                "authorization_id": "", "ambiguous": "false"}), \
                patch.object(orchestrator, "resolve_oci_artifact", return_value=(
                    "sha256:" + "f" * 64, json.dumps({}).encode())):
            with self.assertRaisesRegex(orchestrator.OrchestrationError, "digest does not match"):
                self.dispatch()

    def test_prepare_receipt_requires_successful_exact_publisher_step(self):
        self.api.add_publisher("123", "2", "124", orchestrator.PREPARE_PUBLISH_STEP,
                               "sha256:" + "e" * 64, conclusion="failure")
        with self.assertRaisesRegex(orchestrator.OrchestrationError, "Publisher step"):
            self.dispatch()

    def test_publisher_digest_parser_handles_real_timestamped_log_and_rejects_ambiguous_markers(self):
        self.api.add_publisher("991", "1", "992", orchestrator.EVIDENCE_PUBLISH_STEP,
                               "sha256:" + "e" * 64)
        # A command echo is not an output marker; the timestamped stdout line is.
        self.api.logs_by_job["992"] = (
            "Run echo MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST=sha256:" + "f" * 64 + "\n"
            "2026-10-20T17:19:54.2975796Z MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST=sha256:" + "e" * 64 + "\n")
        self.assertEqual(orchestrator.publisher_logged_digest(
            self.api, "991", "1", orchestrator.EVIDENCE_PUBLISH_STEP,
            "MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST="), "sha256:" + "e" * 64)
        self.api.logs_by_job["992"] += "MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST=sha256:" + "e" * 64 + "\n"
        with self.assertRaisesRegex(orchestrator.OrchestrationError, "missing or ambiguous"):
            orchestrator.publisher_logged_digest(self.api, "991", "1",
                orchestrator.EVIDENCE_PUBLISH_STEP, "MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST=")

    def test_log_redirect_strips_github_token_and_rejects_untrusted_host(self):
        handler = orchestrator._NoAuthGitHubLogRedirect()
        request = __import__("urllib.request").request.Request(
            "https://api.github.com/repos/akhripunov/maxibot/actions/jobs/1/logs",
            headers={"Authorization": "Bearer secret-token"})
        redirected = handler.redirect_request(request, None, 302, "Found", {},
            "https://release-logs.blob.core.windows.net/signed/logs")
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {},
            "https://attacker.example/logs"))

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
            "schema_version": 3, "project": "maxibot", "environment": "production",
            "authorization_mode": "TASK_SCOPED_AUTO", "authorization_id": authorization_id,
            "owner_instruction_scope": "FULL_PRODUCTION_CYCLE",
            "source_instruction_identifier": "OWNER_TASK_MAXIBOT_ME45_CONTROLLER",
            "owner_task_reference": "Direct OWNER authorization for this bounded MAXIBOT release task.",
            "candidate_selection_rule": "EXACT_CURRENT_MAIN_WITHIN_AUTHORIZED_SCOPE",
            "production_baseline_sha": "d" * 40, "task": "Bounded product task",
            "allowed_paths": ["app/bot/menu.py"],
            "allowed_operations": ["integrate", "coalesce", "prepare", "preflight", "deploy", "verify", "rollback", "publish_evidence"],
            "preauthorized_commits": [], "preauthorized_paths": [],
            "acceptance_criteria": ["menu works"],
            "exclusions": ["database", "migrations", "security", "secrets", "payments", "ads", "paid_traffic", "release_infrastructure"],
            "risk_contracts": {},
            "state": "ACTIVE", "revoked": False, "consumed": False,
        }
        record["owner_task_commitment_sha256"] = owner_task_commitment_sha256(record)
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


def test_controller_allows_shared_network_budget_after_native_action_bootstrap():
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/maxibot-auto-preflight.yml').read_text())
    timeout_seconds = workflow['jobs']['dispatch-authorized-deploy']['timeout-minutes'] * 60
    assert orchestrator.CONTROLLER_RETRY_BUDGET_SECONDS == orchestrator.NETWORK_RETRY_BUDGET_SECONDS
    assert timeout_seconds >= orchestrator.CONTROLLER_RETRY_BUDGET_SECONDS + 10 * 60
