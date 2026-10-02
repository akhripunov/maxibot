#!/usr/bin/env python3
"""Rehearse the prepared archive in an empty, isolated production-mode store."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
ROOT = Path(__file__).resolve().parents[1]

def run(*args, env=None, capture=False):
    result = subprocess.run(args, cwd=ROOT, env=env, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, timeout=900)
    return result.stdout.strip() if capture else None

def rehearse(archive, receipt, sha, temp_root):
    """Rehearse production import/capture/parity/reuse in a private containerd store."""
    started = time.monotonic()
    dockerd, docker, ctr, containerd = (shutil.which(name) for name in ('dockerd', 'docker', 'ctr', 'containerd'))
    unshare = shutil.which('unshare')
    if not all((dockerd, docker, ctr, containerd, unshare, shutil.which('sudo'))):
        raise RuntimeError('isolated Docker daemon prerequisites are unavailable')
    root = Path(tempfile.mkdtemp(prefix='maxibot-image-roundtrip-', dir=temp_root))
    socket = root / 'docker.sock'
    pidfile = root / 'dockerd.pid'
    log_path = root / 'dockerd.log'
    host = f'unix://{socket}'
    log = log_path.open('wb')
    daemon = None
    daemon_pid = None
    content_daemon = None
    content_pidfile = root / 'containerd.pid'
    content_socket = root / 'containerd.sock'
    try:
        config = root / 'daemon.json'
        config.write_text(json.dumps({'features': {'containerd-snapshotter': True}}))
        # Own both daemons explicitly; never attach to system containerd.
        content_daemon = subprocess.Popen(
            ['sudo', '-n', 'env', f'PATH={os.environ["PATH"]}', 'sh', '-c',
             'echo $$ > "$1"; shift; exec "$@"', 'maxibot-containerd', str(content_pidfile),
             containerd, '--root', str(root / 'content'), '--state', str(root / 'content-state'),
             '--address', str(content_socket)],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(30):
            if content_daemon.poll() is not None:
                break
            probe = subprocess.run(['sudo', '-n', ctr, '--address', str(content_socket), 'version'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('private containerd did not become ready')
        if content_daemon.poll() is not None:
            raise RuntimeError('private containerd exited before rehearsal')
        daemon = subprocess.Popen(
            ['sudo', '-n', 'env', f'PATH={os.environ["PATH"]}', unshare, '--net', dockerd,
             '--config-file', str(config), '--host', host, '--data-root', str(root / 'data'),
             '--exec-root', str(root / 'exec'), '--pidfile', str(pidfile), '--storage-driver=overlayfs',
             '--containerd', str(content_socket), '--containerd-namespace=moby',
             '--bridge=none', '--iptables=false', '--ip-masq=false',
             '--ip-forward=false'],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        # Wrappers always address the private daemon, even if setup-docker-action
        # exports a Docker context. ctr must address its private managed containerd.
        wrappers = root / 'bin'
        wrappers.mkdir()
        for name, command in {
                'docker': ['sudo', '-n', docker, '--host', host],
                'ctr': ['sudo', '-n', ctr, '--address', str(content_socket)],
        }.items():
            wrapper = wrappers / name
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
            wrapper.chmod(0o700)
        client_env = dict(os.environ, PATH=f'{wrappers}:{os.environ["PATH"]}', DOCKER_HOST=host)
        for key in ('DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH', 'DOCKER_API_VERSION'):
            client_env.pop(key, None)
        ready = False
        for _ in range(90):
            if daemon.poll() is not None:
                break
            probe = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], env=client_env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            if probe.returncode == 0:
                ready = True
                break
            time.sleep(1)
        if not ready:
            log.flush()
            detail = log_path.read_text(errors='replace')[-4000:]
            raise RuntimeError(f'isolated Docker daemon did not become ready: {detail}')

        info = json.loads(run('docker', 'info', '--format', '{{json .}}', env=client_env, capture=True))
        if info['ServerVersion'] != '29.1.3' or info['Driver'] != 'overlayfs' or \
                ['driver-type', 'io.containerd.snapshotter.v1'] not in info.get('DriverStatus', []):
            raise RuntimeError('rehearsal must use production Docker 29.1.3/containerd/overlayfs')
        if info.get('Containerd', {}).get('Address') != str(content_socket):
            raise RuntimeError('rehearsal Docker is not attached to its private containerd')
        daemon_pid = int(pidfile.read_text().strip())
        network_inode = int(run('sudo', '-n', 'stat', '-Lc', '%i',
                                f'/proc/{daemon_pid}/ns/net', capture=True))
        if network_inode == os.stat('/proc/self/ns/net').st_ino:
            raise RuntimeError('rehearsal Docker must not share the build daemon network namespace')
        from prepared_artifact_cache import checksum
        from prepared_image_archive import inspect_prepared_archive
        image = receipt['image']
        expected = receipt['image_id']
        archive_sha = checksum(archive)
        config, platform = inspect_prepared_archive(archive, image, sha)
        if config != expected or platform != 'linux/amd64' or archive_sha != receipt['archive_sha256']:
            raise RuntimeError('archive identity differs from prepared receipt')
        run('docker', 'load', '--input', str(archive), env=client_env)
        verifier = str(ROOT / 'scripts/verify_loaded_prepared_image.py')
        run(sys.executable, verifier, image, expected, sha, env=client_env)
        native = run('docker', 'image', 'inspect', image, '--format', '{{.Id}}', env=client_env, capture=True)
        if native == expected:
            raise RuntimeError('rehearsal did not exercise config/native identity difference')
        name = 'maxibot-negative-proof-' + uuid.uuid4().hex
        cid = run('docker', 'create', '--name', name, '--network', 'none', '--entrypoint', '/bin/true', image, env=client_env, capture=True)
        try:
            actual = run('docker', 'inspect', cid, '--format', '{{.Image}}', env=client_env, capture=True)
            wrong = subprocess.run(['test', actual, '=', expected], timeout=10)
            if wrong.returncode != 1:
                raise RuntimeError('historical config/native comparison not rejected')
            if actual != native:
                raise RuntimeError('native container parity failed')
        finally:
            run('docker', 'rm', cid, env=client_env)
        run(sys.executable, verifier, image, expected, sha, env=client_env)
        elapsed = round(time.monotonic() - started, 1)
        facts = {'MAXIBOT_PRIVATE_REHEARSAL': 'PASS', 'MAXIBOT_PRIVATE_DOCKER_VERSION': '29.1.3',
                 'MAXIBOT_PRIVATE_STORE': 'containerd', 'MAXIBOT_PRIVATE_SNAPSHOTTER': 'overlayfs',
                 'MAXIBOT_PRIVATE_NETWORK_ISOLATED': 'YES', 'MAXIBOT_PRIVATE_CANDIDATE_SHA': sha,
                 'MAXIBOT_PRIVATE_PREPARE_RUN_ID': os.environ.get('GITHUB_RUN_ID', ''),
                 'MAXIBOT_PRIVATE_ARCHIVE_SHA256': archive_sha, 'MAXIBOT_PRIVATE_SECONDS': str(elapsed)}
        if os.environ.get('GITHUB_ENV'):
            with open(os.environ['GITHUB_ENV'], 'a') as target:
                target.write(''.join(f'{key}={value}\n' for key, value in facts.items()))
        print('MAXIBOT_IDENTITY_REGRESSION_REJECTED=PASS')
        print(f'MAXIBOT_PRIVATE_DOCKER_REHEARSAL=PASS SECONDS={elapsed}')
        return facts
    finally:
        if pidfile.exists():
            try:
                daemon_pid = int(pidfile.read_text().strip())
            except (OSError, ValueError):
                pass
        if daemon is not None and daemon.poll() is None:
            if daemon_pid:
                subprocess.run(['sudo', '-n', 'kill', '-TERM', str(daemon_pid)], check=False, timeout=10)
            try:
                daemon.wait(timeout=30)
            except subprocess.TimeoutExpired:
                if daemon_pid:
                    subprocess.run(['sudo', '-n', 'kill', '-KILL', str(daemon_pid)], check=False, timeout=10)
                daemon.kill()
                daemon.wait(timeout=10)
        if content_daemon is not None and content_daemon.poll() is None:
            if content_pidfile.exists():
                content_pid = int(content_pidfile.read_text().strip())
                subprocess.run(['sudo', '-n', 'kill', '-TERM', str(content_pid)], check=False, timeout=10)
            try:
                content_daemon.wait(timeout=30)
            except subprocess.TimeoutExpired:
                if content_pidfile.exists():
                    subprocess.run(['sudo', '-n', 'kill', '-KILL', str(content_pid)], check=False, timeout=10)
                content_daemon.kill()
                content_daemon.wait(timeout=10)
        log.close()
        # dockerd runs as root on hosted runners, so its isolated scratch tree
        # needs narrowly scoped privileged cleanup.
        subprocess.run(['sudo', '-n', 'rm', '-rf', '--', str(root)], check=False, timeout=30)


def main():
    archive = Path(os.environ['IMAGE_ARCHIVE'])
    receipt = json.loads(Path(os.environ['IMAGE_MANIFEST']).read_text())
    sha = os.environ['CANDIDATE_SHA']
    if receipt.get('candidate_sha') != sha or receipt.get('prepare_run_id') != os.environ.get('GITHUB_RUN_ID'):
        raise RuntimeError('rehearsal receipt candidate/run mismatch')
    rehearse(archive, receipt, sha, os.environ.get('RUNNER_TEMP'))

if __name__ == '__main__':
    main()
