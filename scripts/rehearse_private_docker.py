#!/usr/bin/env python3
"""Rehearse archive import in an empty private Docker/containerd store."""
from __future__ import annotations
import hashlib, json, os, shutil, shlex, signal, subprocess, sys, tempfile, time
from pathlib import Path

def main() -> int:
    archive = Path(os.environ["IMAGE_ARCHIVE"]); manifest = Path(os.environ["IMAGE_MANIFEST"]); sha = os.environ["CANDIDATE_SHA"]
    names = {n: shutil.which(n) for n in ("dockerd", "docker", "ctr", "containerd", "unshare", "sudo")}
    if not all(names.values()): raise RuntimeError("private Docker rehearsal prerequisites unavailable")
    image = json.loads(manifest.read_text())["image"]
    root = Path(tempfile.mkdtemp(prefix="maxibot-private-docker-")); log = (root / "daemon.log").open("wb")
    sock, ctrsock, pid, cpid = root / "docker.sock", root / "containerd.sock", root / "dockerd.pid", root / "containerd.pid"
    daemon = content = None
    try:
        content = subprocess.Popen(["sudo", "-n", "env", f"PATH={os.environ['PATH']}", "sh", "-c", 'echo $$ > "$1"; shift; exec "$@"', "maxibot-containerd", str(cpid), names["containerd"], "--root", str(root / "content"), "--state", str(root / "content-state"), "--address", str(ctrsock)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(30):
            if subprocess.run(["sudo", "-n", names["ctr"], "--address", str(ctrsock), "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0: break
            time.sleep(1)
        else: raise RuntimeError("private containerd did not become ready")
        daemon = subprocess.Popen(["sudo", "-n", "env", f"PATH={os.environ['PATH']}", names["unshare"], "--net", names["dockerd"], "--host", f"unix://{sock}", "--data-root", str(root / "data"), "--exec-root", str(root / "exec"), "--pidfile", str(pid), "--storage-driver=overlayfs", "--containerd", str(ctrsock), "--containerd-namespace=moby", "--bridge=none", "--iptables=false", "--ip-masq=false", "--ip-forward=false"], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        wrappers = root / "bin"; wrappers.mkdir()
        (wrappers / "docker").write_text("#!/bin/sh\nexec " + shlex.join(["sudo", "-n", names["docker"], "--host", f"unix://{sock}"]) + ' "$@"\n')
        (wrappers / "ctr").write_text("#!/bin/sh\nexec " + shlex.join(["sudo", "-n", names["ctr"], "--address", str(ctrsock)]) + ' "$@"\n')
        for p in wrappers.iterdir(): p.chmod(0o700)
        env = dict(os.environ, PATH=f"{wrappers}:{os.environ['PATH']}"); env.pop("DOCKER_CONTEXT", None); env["DOCKER_HOST"] = f"unix://{sock}"
        for _ in range(90):
            if subprocess.run(["docker", "info"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0: break
            time.sleep(1)
        else: raise RuntimeError("private dockerd did not become ready")
        info = json.loads(subprocess.check_output(["docker", "info", "--format", "{{json .}}"], env=env, text=True))
        if info.get("ServerVersion") != "29.1.3" or info.get("Driver") != "overlayfs" or ["driver-type", "io.containerd.snapshotter.v1"] not in info.get("DriverStatus", []) or (info.get("Containerd") or {}).get("Address") != str(ctrsock): raise RuntimeError("private Docker/containerd identity mismatch")
        daemon_pid = int(pid.read_text()); ns = int(subprocess.check_output(["sudo", "-n", "stat", "-Lc", "%i", f"/proc/{daemon_pid}/ns/net"], text=True));
        if ns == os.stat("/proc/self/ns/net").st_ino: raise RuntimeError("private dockerd shares build network namespace")
        subprocess.run(["docker", "load", "--input", str(archive)], env=env, check=True, stdout=subprocess.DEVNULL)
        verify_env = dict(env, IMAGE= image, EXPECTED_CONFIG=json.loads(manifest.read_text())["image_id"], CANDIDATE_SHA=sha)
        subprocess.run(["python3", str(Path(__file__).with_name("verify_loaded_prepared_image.py")), image, verify_env["EXPECTED_CONFIG"], sha], env=verify_env, check=True)
        cid = subprocess.check_output(["docker", "create", "--name", "maxibot-negative-proof", "--network", "none", "--entrypoint", "/bin/true", image], env=env, text=True).strip()
        actual = subprocess.check_output(["docker", "inspect", cid, "--format", "{{.Image}}"], env=env, text=True).strip()
        if actual == verify_env["EXPECTED_CONFIG"]: raise RuntimeError("negative config/native comparison was accepted")
        subprocess.run(["docker", "rm", "-f", cid], env=env, check=True, stdout=subprocess.DEVNULL)
        github_env = os.environ.get("GITHUB_ENV")
        if github_env:
            archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            with open(github_env, "a", encoding="utf-8") as handle:
                handle.write("MAXIBOT_PRIVATE_REHEARSAL=PASS\nMAXIBOT_PRIVATE_DOCKER_VERSION=29.1.3\nMAXIBOT_PRIVATE_STORE=containerd\nMAXIBOT_PRIVATE_SNAPSHOTTER=overlayfs\nMAXIBOT_PRIVATE_NETWORK_ISOLATED=YES\n")
                handle.write(f"MAXIBOT_PRIVATE_CANDIDATE_SHA={sha}\nMAXIBOT_PRIVATE_PREPARE_RUN_ID={os.environ.get('GITHUB_RUN_ID', '')}\nMAXIBOT_PRIVATE_ARCHIVE_SHA256={archive_sha}\n")
        print("MAXIBOT_PRIVATE_DOCKER_REHEARSAL=PASS")
        return 0
    finally:
        for proc in (daemon, content):
            if proc and proc.poll() is None: proc.send_signal(signal.SIGTERM)
        log.close(); shutil.rmtree(root, ignore_errors=True)

if __name__ == "__main__":
    raise SystemExit(main())
