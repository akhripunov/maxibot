import copy
import json
import pytest
from scripts import verify_loaded_prepared_image as proof

SHA = 'a' * 40
LABELS = {k: SHA for k in ('org.opencontainers.image.revision', 'org.opencontainers.image.source-sha', 'org.maxibot.release.sha')}

@pytest.mark.parametrize('fault', [None, 'manifest_bytes', 'config_bytes', 'config_reference', 'rootfs', 'platform', 'labels', 'descriptor', 'container'])
def test_small_content_proof_rejects_corruption_without_export(monkeypatch, fault):
    config = {'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': LABELS}, 'rootfs': {'diff_ids': ['sha256:' + '3'*64]}}
    if fault == 'labels':
        config = copy.deepcopy(config); config['config']['Labels']['org.maxibot.release.sha'] = 'b'*40
    config_bytes = json.dumps(config).encode()
    expected = proof.digest(config_bytes)
    manifest = {'schemaVersion': 2, 'config': {'digest': expected}, 'layers': [{}]}
    if fault == 'config_reference': manifest['config']['digest'] = 'sha256:' + '9'*64
    manifest_bytes = json.dumps(manifest).encode(); native_id = proof.digest(manifest_bytes)
    native = {'Id': native_id, 'Os': 'linux', 'Architecture': 'amd64', 'Descriptor': {'digest': native_id, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}, 'Config': {'Labels': LABELS}, 'RootFS': {'Layers': config['rootfs']['diff_ids']}}
    if fault == 'rootfs': native['RootFS'] = {'Layers': ['wrong']}
    if fault == 'platform': native['Architecture'] = 'arm64'
    if fault == 'descriptor': native['Descriptor']['mediaType'] = 'application/vnd.oci.image.index.v1+json'
    calls=[]
    def run(*args):
        calls.append(args)
        assert args[:3] != ('docker', 'image', 'save')
        if args[:3] == ('docker', 'image', 'inspect'): return json.dumps([native]).encode()
        if args[:2] == ('docker', 'info'): return b'{"Containerd":{"Address":"/private.sock"}}'
        if args[0] == 'ctr':
            assert args[2] == '/private.sock'
            if args[-1] == native_id: return b'broken' if fault == 'manifest_bytes' else manifest_bytes
            return b'broken' if fault == 'config_bytes' else config_bytes
        if args[:2] == ('docker', 'create'): return b'our-container'
        if args[:2] == ('docker', 'inspect'): return (('wrong' if fault == 'container' else native_id)+'\n').encode()
        raise AssertionError(args)
    monkeypatch.setattr(proof, 'run', run)
    removed=[]
    monkeypatch.setattr(proof.subprocess, 'run', lambda args, **kw: removed.append(args))
    if fault:
        with pytest.raises(RuntimeError): proof.main(['proof', 'maxibot-vps-api:'+SHA, expected, SHA])
    else:
        assert proof.main(['proof', 'maxibot-vps-api:'+SHA, expected, SHA]) == 0
        assert removed
    assert len([c for c in calls if c[0]=='ctr']) <= 2
