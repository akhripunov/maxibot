"""Direct OWNER task parity with GPoint; no live services or credentials."""
import copy
import hashlib
import json
import subprocess
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import release_authorization as auth

BASE = 'a' * 40
SHA = 'b' * 40
ID = 'maxibot-owner-task-parity-tests'
OPS = ['integrate', 'coalesce', 'prepare', 'preflight', 'deploy', 'verify', 'rollback', 'publish_evidence']
GATES = ['exact_candidate', 'complete_scope', 'prepare_provenance', 'immutable_artifact', 'security_review', 'preflight', 'rollback_readiness', 'worker_compatibility', 'postdeploy_health', 'reconciliation']


def task_record(paths=None, baseline=BASE):
    paths = paths or ['scripts/release_orchestrator.py']
    record = dict(schema_version=3, authorization_id=ID, project='maxibot', environment='production',
        authorization_mode='TASK_SCOPED_AUTO', owner_instruction_scope='FULL_PRODUCTION_CYCLE',
        source_instruction_identifier='OWNER_TASK_MAXIBOT_PARITY_TEST_20261020',
        owner_task_reference='Direct OWNER release task in the existing authorized DevOps chat.',
        candidate_selection_rule='EXACT_CURRENT_MAIN_WITHIN_AUTHORIZED_SCOPE',
        production_baseline_sha=baseline, task='Complete one bounded MAXIBOT release infrastructure task.',
        allowed_paths=paths, allowed_operations=OPS, preauthorized_commits=[], preauthorized_paths=[],
        acceptance_criteria=['All canonical release gates pass.'],
        exclusions=['database', 'migrations', 'security', 'secrets', 'payments', 'ads', 'paid_traffic'],
        risk_contracts={'release_infrastructure':dict(allowed_paths=paths, allowed_operations=OPS,
            baseline_sha=baseline, risk_description='Explicitly approved release infrastructure parity task.',
            required_gates=GATES, backup_plan='Canonical restricted database backup is validated before switch.',
            rollback_plan='Retain current exact images and restore only through canonical rollback.',
            stop_conditions=['Stop on production baseline drift.', 'Stop on ambiguous apply or failed safety gate.'])},
        state='ACTIVE', revoked=False, consumed=False)
    return sign(record)


def sign(record):
    record['owner_task_commitment_sha256'] = auth.owner_task_commitment_sha256(record)
    return record


def check(record, paths=None):
    return auth.validate_task_authorization(record=record, project='maxibot', authorization_id=ID,
        candidate_sha=SHA, current_main_sha=SHA, production_sha=BASE,
        changed_paths=paths or ['scripts/release_orchestrator.py'])


def test_direct_owner_task_needs_no_linear_reference():
    record = task_record()
    assert 'source_reference' not in record
    assert check(record)['source_instruction_identifier'] == record['source_instruction_identifier']


@pytest.mark.parametrize('field,value', [('allowed_paths', ['app/bot/menu.py']),
    ('owner_task_reference', 'tampered owner reference'), ('production_baseline_sha', 'c' * 40)])
def test_commitment_rejects_scope_or_owner_reference_tampering(field, value):
    record = task_record(); record[field] = value
    with pytest.raises(auth.AuthorizationError, match='commitment'):
        check(record)


@pytest.mark.parametrize('field', ['source_instruction_identifier', 'owner_task_reference', 'owner_task_commitment_sha256'])
def test_missing_direct_owner_evidence_blocks(field):
    record = task_record(); record.pop(field)
    with pytest.raises(auth.AuthorizationError):
        check(record)


@pytest.mark.parametrize('field', ['allowed_paths', 'allowed_operations', 'baseline_sha', 'required_gates', 'backup_plan', 'rollback_plan', 'stop_conditions'])
def test_incomplete_infrastructure_contract_blocks(field):
    record = task_record(); record['risk_contracts']['release_infrastructure'].pop(field); sign(record)
    with pytest.raises(auth.AuthorizationError):
        check(record)


@pytest.mark.parametrize('path', ['app/security/auth.py', 'alembic/versions/0100_extra.py', '.env', 'app/ads/decision.py'])
def test_infrastructure_contract_never_authorizes_other_owner_only_categories(path):
    record = task_record([path])
    with pytest.raises(auth.AuthorizationError):
        check(record, [path])


def test_no_contract_requires_infrastructure_exclusion():
    record = task_record(); record['risk_contracts'] = {}; sign(record)
    with pytest.raises(auth.AuthorizationError):
        check(record)


def test_candidate_sha_cannot_be_pinned_in_task():
    record = task_record(); record['selected_candidate_sha'] = SHA
    with pytest.raises(auth.AuthorizationError):
        check(record)


def test_prepare_resolves_the_precreated_record_for_exact_candidate_scope(tmp_path):
    git, baseline = git_fixture(tmp_path)
    paths = ['app/bot/menu.py', 'tests/regression/test_owner_only_channel_menu_buttons.py']
    record = task_record(paths, baseline=baseline)
    record_path = tmp_path / auth.record_path(ID)
    record_path.parent.mkdir()
    record_path.write_text(json.dumps(record))
    git('add', '.')
    git('commit', '-qm', 'record owner task before implementation')
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('authorized change\n')
    git('add', '.')
    git('commit', '-qm', 'implement authorized task')
    candidate = git('rev-parse', 'HEAD')

    result = auth.resolve_prepare_authorization(
        tmp_path, candidate, candidate, lambda _authorization_id, _state: False)

    assert result['authorization_id'] == ID
    assert result['production_baseline_sha'] == baseline
    assert result['changed_paths'] == paths


def test_prepare_does_not_resolve_late_or_out_of_scope_authorization(tmp_path):
    git, baseline = git_fixture(tmp_path)
    paths = ['app/bot/menu.py', 'tests/regression/test_owner_only_channel_menu_buttons.py']
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('change\n')
    git('add', '.')
    git('commit', '-qm', 'implement before owner task record')
    record = task_record(paths[:1], baseline=baseline)
    record_path = tmp_path / auth.record_path(ID)
    record_path.parent.mkdir()
    record_path.write_text(json.dumps(record))
    git('add', '.')
    git('commit', '-qm', 'add authorization after implementation')
    candidate = git('rev-parse', 'HEAD')

    result = auth.resolve_prepare_authorization(
        tmp_path, candidate, candidate, lambda _authorization_id, _state: False)

    assert result is None


def test_prepare_rejects_stale_main_and_inactive_durable_authorization(tmp_path):
    git, baseline = git_fixture(tmp_path)
    record = task_record(['app/bot/menu.py'], baseline=baseline)
    record_path = tmp_path / auth.record_path(ID)
    record_path.parent.mkdir()
    record_path.write_text(json.dumps(record))
    git('add', '.')
    git('commit', '-qm', 'record owner task')
    target = tmp_path / 'app/bot/menu.py'
    target.parent.mkdir(parents=True)
    target.write_text('authorized change\n')
    git('add', '.')
    git('commit', '-qm', 'implement authorized task')
    candidate = git('rev-parse', 'HEAD')

    with pytest.raises(auth.AuthorizationError, match='exact current main'):
        auth.resolve_prepare_authorization(
            tmp_path, candidate, baseline, lambda _authorization_id, _state: False)
    assert auth.resolve_prepare_authorization(
        tmp_path, candidate, candidate,
        lambda _authorization_id, state: state == 'consumed') is None


def test_prepare_fails_closed_when_authorization_state_cannot_be_read(tmp_path):
    git, baseline = git_fixture(tmp_path)
    record = task_record(['app/bot/menu.py'], baseline=baseline)
    record_path = tmp_path / auth.record_path(ID)
    record_path.parent.mkdir()
    record_path.write_text(json.dumps(record))
    git('add', '.'); git('commit', '-qm', 'record owner task')
    target = tmp_path / 'app/bot/menu.py'
    target.parent.mkdir(parents=True); target.write_text('change\n')
    git('add', '.'); git('commit', '-qm', 'implementation')
    candidate = git('rev-parse', 'HEAD')

    def unavailable(_authorization_id, _state):
        raise RuntimeError('GitHub API unavailable')

    with pytest.raises(RuntimeError, match='unavailable'):
        auth.resolve_prepare_authorization(tmp_path, candidate, candidate, unavailable)


def git_fixture(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, text=True, stderr=subprocess.DEVNULL).strip()
    git('init', '-q'); git('config', 'user.name', 'Task test'); git('config', 'user.email', 'test@example.invalid')
    (tmp_path/'README.md').write_text('baseline');git('add','.');git('commit','-qm','baseline')
    return git, git('rev-parse','HEAD')


def test_exact_historical_inventory_and_new_descendants(tmp_path):
    git, baseline = git_fixture(tmp_path)
    (tmp_path/'scripts').mkdir(); (tmp_path/'scripts/release_orchestrator.py').write_text('prepared')
    git('add','.');git('commit','-qm','historical prepared change'); historical=git('rev-parse','HEAD')
    record=task_record(baseline=baseline);record['preauthorized_commits']=[historical];record['preauthorized_paths']=['scripts/release_orchestrator.py'];sign(record)
    path=tmp_path/auth.record_path(ID);path.parent.mkdir();path.write_text(json.dumps(record))
    git('add','.');git('commit','-qm','record owner task')
    (tmp_path/'scripts/release_orchestrator.py').write_text('reviewed new fix')
    git('add','.');git('commit','-qm','implement after owner task');candidate=git('rev-parse','HEAD')
    assert auth.candidate_scope_diff(tmp_path,ID,baseline,candidate)==['scripts/release_orchestrator.py']
    # Modifying the immutable record cannot authorize an unlisted old commit.
    record['preauthorized_commits']=[];path.write_text(json.dumps(record));git('add','.');git('commit','-qm','tamper')
    with pytest.raises(auth.AuthorizationError):
        auth.candidate_scope_diff(tmp_path,ID,baseline,git('rev-parse','HEAD'))


def test_unlisted_historical_commit_is_rejected(tmp_path):
    git,baseline=git_fixture(tmp_path)
    (tmp_path/'scripts').mkdir();(tmp_path/'scripts/release_orchestrator.py').write_text('old')
    git('add','.');git('commit','-qm','old implementation')
    record=task_record(baseline=baseline)
    path=tmp_path/auth.record_path(ID);path.parent.mkdir();path.write_text(json.dumps(record))
    git('add','.');git('commit','-qm','owner task')
    with pytest.raises(auth.AuthorizationError,match='history'):
        auth.candidate_scope_diff(tmp_path,ID,baseline,git('rev-parse','HEAD'))


def test_exact_historical_merge_inventory_is_required(tmp_path):
    git,baseline=git_fixture(tmp_path)
    git('branch','baseline-branch');git('switch','-qc','prepared')
    (tmp_path/'scripts').mkdir();(tmp_path/'scripts/release_orchestrator.py').write_text('prepared')
    git('add','.');git('commit','-qm','prepared fix');prepared=git('rev-parse','HEAD')
    git('switch','-q','baseline-branch');git('merge','--no-ff','prepared','-m','historical reviewed merge')
    merge=git('rev-parse','HEAD')
    record=task_record(baseline=baseline);record['preauthorized_commits']=sorted([prepared,merge]);record['preauthorized_paths']=['scripts/release_orchestrator.py'];sign(record)
    path=tmp_path/auth.record_path(ID);path.parent.mkdir();path.write_text(json.dumps(record))
    git('add','.');git('commit','-qm','new owner task')
    assert auth.candidate_scope_diff(tmp_path,ID,baseline,git('rev-parse','HEAD'))==['scripts/release_orchestrator.py']


def test_predecessor_claim_requires_same_immutable_task_history(tmp_path):
    git,baseline=git_fixture(tmp_path)
    record=task_record(baseline=baseline)
    path=tmp_path/auth.record_path(ID);path.parent.mkdir();path.write_text(json.dumps(record))
    git('add','.');git('commit','-qm','owner task')
    (tmp_path/'scripts').mkdir();(tmp_path/'scripts/release_orchestrator.py').write_text('first')
    git('add','.');git('commit','-qm','first candidate');first=git('rev-parse','HEAD')
    (tmp_path/'scripts/release_orchestrator.py').write_text('replacement')
    git('add','.');git('commit','-qm','replacement candidate');candidate=git('rev-parse','HEAD')
    auth.validate_claim_candidate(tmp_path,ID,record,{'object':{'sha':first,'type':'commit'}},candidate)
    with pytest.raises(auth.AuthorizationError):
        auth.validate_claim_candidate(tmp_path,ID,record,{'object':{'sha':baseline,'type':'commit'}},candidate)
    with pytest.raises(auth.AuthorizationError):
        auth.validate_claim_candidate(tmp_path,ID,record,{'object':{'sha':first,'type':'tag'}},candidate)


def test_canonical_preflight_reports_runtime_sha_and_apply_checks_baseline_before_upload():
    shell=(ROOT/'scripts/release_prod.sh').read_text()
    wrapper=(ROOT/'scripts/run_release_workflow.py').read_text()
    assert 'check_value PROD_RELEASE_SHA ' in shell
    assert 'EXPECTED_PRODUCTION_SHA' in shell
    assert shell.index('authorization production baseline drifted') < shell.index('timed_stage SOURCE_UPLOAD')
    assert shell.index('authorization production database head drifted') < shell.index('timed_stage SOURCE_UPLOAD')
    assert 'env["EXPECTED_PRODUCTION_SHA"] = result["production_baseline_sha"]' in wrapper


def test_canonical_apply_lock_is_exclusive_and_released_only_by_its_owner(tmp_path):
    shell=(ROOT/'scripts/release_prod.sh').read_text()
    start=shell.index('acquire_release_lock() {')
    end=shell.index('\n# End release lock helpers',start)
    helpers=shell[start:end]
    import os
    env={**os.environ,'MAXIBOT_RELEASE_LOCK_DIR':str(tmp_path/'release.lock')}
    prefix='set -eu\ndie() { echo "$*" >&2; exit 1; }\n'+helpers+'\n'
    first=subprocess.Popen(['bash','-c',prefix+'acquire_release_lock\necho locked\nread -r done\nrelease_owned_lock'],env=env,text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    try:
        assert first.stdout.readline().strip()=='locked'
        second=subprocess.run(['bash','-c',prefix+'acquire_release_lock'],env=env,text=True,capture_output=True)
        assert second.returncode!=0
        assert 'release lock exists' in second.stderr
        first.communicate('done\n',timeout=5)
        assert first.returncode==0
        assert not (tmp_path/'release.lock').exists()
        stale=tmp_path/'release.lock';stale.mkdir();(stale/'owner').write_text('stale')
        failed=subprocess.run(['bash','-c',prefix+'acquire_release_lock'],env=env,text=True,capture_output=True)
        assert failed.returncode!=0
        assert (stale/'owner').read_text()=='stale'
    finally:
        if first.poll() is None:first.kill();first.wait()
