#!/usr/bin/env python3
"""Prove a loaded MAXIBOT image's manifest, config, labels and rootfs identity."""
from __future__ import annotations
import hashlib, json, re, subprocess, sys, uuid

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
    descriptor = native.get("Descriptor") or {}
    if descriptor.get("digest") != native_id or descriptor.get("mediaType") not in ("application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"):
        raise RuntimeError("loaded image lacks a single-platform manifest descriptor")
    info = json.loads(run("docker", "info", "--format", "{{json .}}"))
    address = (info.get("Containerd") or {}).get("Address")
    if not address:
        raise RuntimeError("Docker containerd content address is unavailable")
    manifest_bytes = run("ctr", "--address", address, "-n", "moby", "content", "get", native_id)
    if digest(manifest_bytes) != native_id:
        raise RuntimeError("loaded image manifest checksum mismatch")
    manifest = json.loads(manifest_bytes)
    if manifest.get("schemaVersion") != 2 or manifest.get("config", {}).get("digest") != expected:
        raise RuntimeError("loaded image config identity mismatch")
    config_bytes = run("ctr", "--address", address, "-n", "moby", "content", "get", expected)
    if digest(config_bytes) != expected:
        raise RuntimeError("loaded image config checksum mismatch")
    config = json.loads(config_bytes)
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise RuntimeError("verified config platform mismatch")
    labels = (config.get("config") or {}).get("Labels") or {}
    if any(labels.get(key) != sha for key in ("org.opencontainers.image.revision", "org.opencontainers.image.source-sha", "org.maxibot.release.sha")):
        raise RuntimeError("loaded image source labels mismatch")
    inspect_labels = (native.get("Config") or {}).get("Labels") or {}
    if any(inspect_labels.get(key) != value for key, value in labels.items() if key.startswith("org.")):
        raise RuntimeError("Docker inspect labels differ from verified config")
    layers = config.get("rootfs", {}).get("diff_ids") or []
    if not layers or layers != native.get("RootFS", {}).get("Layers") or len(layers) != len(manifest.get("layers", [])):
        raise RuntimeError("loaded image rootfs differs from verified config")
    container = "maxibot-loaded-proof-" + uuid.uuid4().hex
    created = False
    try:
        run("docker", "create", "--name", container, "--network", "none", "--entrypoint", "/bin/true", image)
        created = True
        container_image = run("docker", "inspect", container, "--format", "{{.Image}}").decode().strip()
        if container_image != native_id:
            raise RuntimeError("created container image identity differs from native image")
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
    print(f"MAXIBOT_LOADED_IMAGE_PROOF=PASS IMAGE={image} CONFIG={expected} NATIVE={native_id}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
