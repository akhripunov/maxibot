#!/usr/bin/env python3
"""Prove a loaded MAXIBOT image's manifest, config, labels and rootfs identity."""
from __future__ import annotations
import hashlib, json, re, subprocess, sys, tempfile, tarfile
from pathlib import Path

def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()

def run(*args: str) -> bytes:
    return subprocess.check_output(args, timeout=60, stderr=subprocess.STDOUT)

def main(argv: list[str]) -> int:
    if len(argv) != 4 or not re.fullmatch(r"[a-f0-9]{40}", argv[3]):
        raise SystemExit("usage: verify_loaded_prepared_image.py IMAGE EXPECTED_CONFIG_SHA CANDIDATE_SHA")
    image, expected, sha = argv[1:]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
        raise SystemExit("invalid expected config digest")
    native = json.loads(run("docker", "image", "inspect", image))[0]
    native_id = native.get("Id", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", native_id):
        raise RuntimeError("invalid native Docker image ID")
    if native.get("Os") != "linux" or native.get("Architecture") != "amd64":
        raise RuntimeError("loaded image platform mismatch")
    with tempfile.TemporaryDirectory(prefix="maxibot-loaded-proof-") as temp:
        archive = Path(temp) / "image.tar"
        archive.write_bytes(run("docker", "image", "save", image))
        with tarfile.open(archive) as bundle:
            manifest = json.loads(bundle.extractfile("manifest.json").read())[0]
            config_name = manifest["Config"]
            config_bytes = bundle.extractfile(config_name).read()
            config_id = digest(config_bytes)
            if config_id != expected:
                raise RuntimeError(f"loaded image config identity mismatch: expected {expected}, actual {config_id}")
            config = json.loads(config_bytes)
            labels = (config.get("config") or {}).get("Labels") or {}
            if labels.get("org.opencontainers.image.revision") != sha or labels.get("org.opencontainers.image.source-sha") != sha or labels.get("org.maxibot.release.sha") != sha:
                raise RuntimeError("loaded image source labels mismatch")
            layers = config.get("rootfs", {}).get("diff_ids") or []
            if not layers or len(layers) != len(manifest.get("Layers") or []):
                raise RuntimeError("loaded image rootfs differs from verified config")
    container = "maxibot-loaded-proof"
    try:
        run("docker", "create", "--name", container, "--network", "none", "--entrypoint", "/bin/true", image)
        created = run("docker", "inspect", container, "--format", "{{.Image}}").decode().strip()
        if created != native_id:
            raise RuntimeError("created container image identity differs from native image")
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
    print(f"MAXIBOT_LOADED_IMAGE_PROOF=PASS IMAGE={image} CONFIG={expected} NATIVE={native_id}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
