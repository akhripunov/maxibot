from scripts import release_coalescing as coalescing

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def push_event(sha):
    return {"ref": "refs/heads/main", "after": sha,
            "repository": {"full_name": "akhripunov/maxibot"}}


class FakeAPI:
    def __init__(self, main_sha, runs=()):
        self.main_sha = main_sha
        self.runs = list(runs)
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path))
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.main_sha}}
        if path.startswith("actions/workflows/maxibot-prepare.yml/runs"):
            return {"workflow_runs": self.runs}
        raise AssertionError(path)

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        return {}


def prepare_run(sha, run_id, status="in_progress", event="push"):
    return {"id": run_id, "head_sha": sha, "head_branch": "main",
            "status": status, "event": event,
            "path": ".github/workflows/maxibot-prepare.yml"}


def test_a_b_candidates_are_superseded_and_only_stable_c_is_dispatched():
    api = FakeAPI(SHA_C, [prepare_run(SHA_A, 1), prepare_run(SHA_B, 2)])
    sleeps = []
    result_a = coalescing.coalesce_candidate(push_event(SHA_A), api, sleep=sleeps.append)
    result_b = coalescing.coalesce_candidate(push_event(SHA_B), api, sleep=sleeps.append)
    result_c = coalescing.coalesce_candidate(push_event(SHA_C), api, sleep=sleeps.append)

    assert result_a["state"] == "SUPERSEDED"
    assert result_b["state"] == "SUPERSEDED"
    assert result_c["state"] == "PREPARE_DISPATCHED"
    assert sleeps == [180, 180, 180]
    cancels = [call for call in api.calls if call[0] == "POST" and call[1].endswith("/cancel")]
    assert {call[1].split("/")[-2] for call in cancels} == {"1", "2"}
    dispatches = [call for call in api.calls if call[0] == "POST" and call[1].endswith("/dispatches")]
    assert len(dispatches) == 1
    assert dispatches[0][2]["inputs"]["candidate"] == SHA_C
    assert dispatches[0][2]["inputs"]["orchestrator_run_id"] == "local"


def test_active_same_sha_prepare_is_not_duplicated():
    api = FakeAPI(SHA_C, [prepare_run(SHA_C, 3)])
    result = coalescing.coalesce_candidate(push_event(SHA_C), api, sleep=lambda _: None)
    assert result["state"] == "PREPARE_ALREADY_ACTIVE"
    assert not any(call[0] == "POST" and call[1].endswith("/dispatches") for call in api.calls)


def test_uncertain_cancel_fails_closed_before_prepare_dispatch():
    api = FakeAPI(SHA_C, [prepare_run(SHA_A, 1)])
    api.post = lambda path, body: (_ for _ in ()).throw(coalescing.APIError(503, path=path))
    try:
        coalescing.coalesce_candidate(push_event(SHA_C), api, sleep=lambda _: None)
    except coalescing.OrchestrationError as error:
        assert "cancel" in str(error).lower()
    else:
        raise AssertionError("uncertain cancellation must fail closed")
    assert not any(call[0] == "POST" and call[1].endswith("/dispatches") for call in api.calls)


def test_uncertain_dispatch_is_reconciled_by_unique_controller_run_not_retried():
    class AcceptedButTimedOut(FakeAPI):
        def post(self, path, body):
            self.calls.append(("POST", path, body))
            if path.endswith("/dispatches"):
                self.runs.append({"id": 44, "head_sha": SHA_C, "head_branch": "main",
                                  "event": "workflow_dispatch", "path": coalescing.PREPARE_PATH,
                                  "display_title": "Prepare MAXIBOT %s coalescer/owner-run" % SHA_C,
                                  "run_attempt": 1, "status": "queued"})
                raise coalescing.APIError(503, path)
            return {}

    api = AcceptedButTimedOut(SHA_C)
    result = coalescing.coalesce_candidate(push_event(SHA_C), api, sleep=lambda _: None,
                                            controller_run_id="owner-run")
    assert result["state"] == "PREPARE_DISPATCH_RECONCILED"
    assert result["prepare_run_id"] == "44"
    assert sum(call[0] == "POST" and call[1].endswith("/dispatches") for call in api.calls) == 1


def test_coalescing_workflow_is_the_only_main_prepare_trigger():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    prepare = (root / ".github/workflows/maxibot-prepare.yml").read_text()
    coalescer = (root / ".github/workflows/maxibot-candidate-coalescer.yml").read_text()
    assert "push:" not in prepare.split("permissions:", 1)[0]
    assert "actions: write" in coalescer
    assert "maxibot-production" in coalescer
    assert "cancel-in-progress: true" in coalescer
