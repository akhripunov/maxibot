#!/usr/bin/env python3
"""MAXIBOT-only immutable GitHub Release bundles and evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

try:
    from scripts.prepared_image_archive import inspect_prepared_archive
except ModuleNotFoundError:
    from prepared_image_archive import inspect_prepared_archive

REPOSITORY = "akhripunov/maxibot"
MAX_RELEASE_ASSET_BYTES = 2 * 1024**3
MAX_BUNDLE_BYTES = 4 * 1024**3
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]*$")
ASSET_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
PREPARE_ASSET_NAME = "bundle.zip"
EVIDENCE_ASSET_NAME = "evidence.zip"
PREPARE_MEMBERS = ("maxibot-image-{sha}.tar.gz", "maxibot-image-manifest.json",
                   "maxibot-prepare-result.json")
EVIDENCE_MEMBERS = ("result.json", "image-manifest.json", "prepare-receipt.json",
                    "evidence-manifest.json", "SHA256SUMS")


class ReleaseAssetError(RuntimeError):
    """A release asset or its provenance is missing or inconsistent."""


def release_tag(kind: str, sha: str, run_id: str | int, attempt: str | int) -> str:
    if (kind not in {"prepare", "evidence"} or not SHA_RE.fullmatch(str(sha))
            or not POSITIVE_INT_RE.fullmatch(str(run_id))
            or not POSITIVE_INT_RE.fullmatch(str(attempt))):
        raise ReleaseAssetError("Invalid MAXIBOT immutable Release identity")
    return f"maxibot-{kind}-{sha}-{run_id}-{attempt}"


def resolve_asset(kind: str, sha: str, run_id: str | int, attempt: str | int,
                  name: str, github_get) -> dict:
    """Freshly validate an immutable release, exact tag ref, and single asset."""
    tag = release_tag(kind, sha, run_id, attempt)
    release = github_get(f"releases/tags/{tag}")
    if (release.get("tag_name") != tag or release.get("draft") is not False
            or release.get("immutable") is not True):
        raise ReleaseAssetError("MAXIBOT Release is missing, draft, or mutable")
    ref = github_get(f"git/ref/tags/{tag}")
    obj = ref.get("object") or {}
    if obj.get("type") != "commit" or obj.get("sha") != sha:
        raise ReleaseAssetError("Immutable Release tag does not point to the exact candidate SHA")
    expected_name = PREPARE_ASSET_NAME if kind == "prepare" else EVIDENCE_ASSET_NAME
    if name != expected_name:
        raise ReleaseAssetError("Unexpected MAXIBOT Release asset name")
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == name]
    if len(matches) != 1 or len(release.get("assets", [])) != 1:
        raise ReleaseAssetError("Expected exactly one immutable MAXIBOT Release asset")
    asset = matches[0]
    if (not POSITIVE_INT_RE.fullmatch(str(asset.get("id", "")))
            or asset.get("state") != "uploaded"
            or type(asset.get("size")) is not int
            or asset["size"] <= 0
            or asset["size"] >= MAX_RELEASE_ASSET_BYTES
            or not ASSET_DIGEST_RE.fullmatch(str(asset.get("digest", "")))):
        raise ReleaseAssetError("Immutable Release asset size, state, id, or SHA256 is invalid")
    return asset


def _expected_prepare_members(sha: str) -> tuple[str, ...]:
    return tuple(member.format(sha=sha) for member in PREPARE_MEMBERS)


def _safe_read_zip(payload: bytes | io.BufferedIOBase, expected: tuple[str, ...]) -> dict[str, bytes]:
    try:
        source = io.BytesIO(payload) if isinstance(payload, bytes) else payload
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise ReleaseAssetError("Unexpected or duplicate Release bundle members")
            if sum(entry.file_size for entry in entries) > MAX_BUNDLE_BYTES:
                raise ReleaseAssetError("Expanded Release bundle exceeds the configured size limit")
            result = {}
            for entry in entries:
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or entry.is_dir() or entry.filename.startswith(("/", "\\")):
                    raise ReleaseAssetError("Release bundle members must be regular files")
                if ".." in Path(entry.filename).parts:
                    raise ReleaseAssetError("Release bundle contains a traversal path")
                with archive.open(entry) as source:
                    result[entry.filename] = source.read()
            return result
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseAssetError("Release bundle is not a readable ZIP archive") from exc


def _json_member(members: dict[str, bytes], name: str) -> dict:
    try:
        value = json.loads(members[name].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseAssetError(f"Release bundle JSON member {name} is invalid") from exc
    if not isinstance(value, dict):
        raise ReleaseAssetError(f"Release bundle JSON member {name} must be an object")
    return value


def validate_prepare_bundle(members: dict[str, bytes], sha: str, run_id: str | int,
                            attempt: str | int, *, inspect_image: bool = True) -> dict:
    expected = set(_expected_prepare_members(sha))
    if set(members) != expected:
        raise ReleaseAssetError("Prepare bundle members do not match the exact candidate")
    image_name, manifest_name, receipt_name = _expected_prepare_members(sha)
    manifest = _json_member(members, manifest_name)
    receipt = _json_member(members, receipt_name)
    image_bytes = members[image_name]
    digest = hashlib.sha256(image_bytes).hexdigest()
    expected_labels = {
        "org.opencontainers.image.revision": sha,
        "org.opencontainers.image.source-sha": sha,
        "org.maxibot.release.sha": sha,
    }
    if (manifest.get("schema") not in {"MAXIBOT_PREPARED_IMAGE_V1", "MAXIBOT_PREPARED_IMAGE_V2"}
            or manifest.get("candidate_sha") != sha
            or str(manifest.get("prepare_run_id")) != str(run_id)
            or str(manifest.get("prepare_run_attempt")) != str(attempt)
            or manifest.get("image") != f"maxibot-vps-api:{sha}"
            or manifest.get("platform") != "linux/amd64"
            or manifest.get("labels") != expected_labels
            or manifest.get("archive_sha256") != digest
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(manifest.get("image_id", "")))):
        raise ReleaseAssetError("Prepared image manifest provenance or checksum mismatch")
    if manifest.get("schema") == "MAXIBOT_PREPARED_IMAGE_V2":
        try:
            from scripts.maxibot_ghcr import validate_oci_image
        except ModuleNotFoundError:
            from maxibot_ghcr import validate_oci_image
        try:
            validate_oci_image(manifest, sha, str(run_id), str(attempt))
        except ValueError as exc:
            raise ReleaseAssetError("GHCR image digest provenance mismatch") from exc
        if manifest.get("config_digest") != manifest.get("image_id"):
            raise ReleaseAssetError("GHCR config digest differs from prepared archive identity")
    if (receipt.get("schema") != "MAXIBOT_PREPARE_RECEIPT_V1"
            or receipt.get("candidate_sha") != sha
            or str(receipt.get("github_run_id")) != str(run_id)
            or str(receipt.get("github_run_attempt")) != str(attempt)
            or receipt.get("status") != "READY"
            or receipt.get("tests") != "success"
            or receipt.get("frozen_path_guard") != "success"
            or receipt.get("linux_amd64_image") != "success"
            or receipt.get("private_docker_rehearsal") != "PASS"
            or receipt.get("rehearsal_candidate_sha") != sha
            or str(receipt.get("rehearsal_prepare_run_id")) != str(run_id)
            or receipt.get("rehearsal_archive_sha256") != digest):
        raise ReleaseAssetError("Prepare receipt is incomplete or does not match this run")
    try:
        image_id, platform = inspect_prepared_archive(
            io.BytesIO(image_bytes), manifest["image"], sha
        ) if inspect_image else (manifest["image_id"], manifest["platform"])
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise ReleaseAssetError("Prepared Docker archive config/platform/labels are invalid") from exc
    if image_id != manifest.get("image_id") or platform != "linux/amd64":
        raise ReleaseAssetError("Docker archive config identity, labels, or platform mismatch")
    return {"manifest": manifest, "receipt": receipt, "image_sha256": digest}


def validate_prepare_directory(directory: Path, sha: str, run_id: str | int,
                               attempt: str | int) -> dict:
    """Verify extracted bundle files without loading a Docker image into memory."""
    image_name, manifest_name, receipt_name = _expected_prepare_members(sha)
    expected = {image_name, manifest_name, receipt_name}
    if not directory.is_dir() or set(item.name for item in directory.iterdir()) != expected:
        raise ReleaseAssetError("Prepare bundle directory members do not match exact candidate")
    if any(item.is_symlink() or not item.is_file() for item in directory.iterdir()):
        raise ReleaseAssetError("Prepare bundle members must be regular files")
    manifest = _json_member({manifest_name: (directory / manifest_name).read_bytes()}, manifest_name)
    receipt = _json_member({receipt_name: (directory / receipt_name).read_bytes()}, receipt_name)
    archive = directory / image_name
    digest = checksum(archive)
    labels = {
        "org.opencontainers.image.revision": sha,
        "org.opencontainers.image.source-sha": sha,
        "org.maxibot.release.sha": sha,
    }
    if (manifest.get("schema") not in {"MAXIBOT_PREPARED_IMAGE_V1", "MAXIBOT_PREPARED_IMAGE_V2"}
            or manifest.get("candidate_sha") != sha
            or str(manifest.get("prepare_run_id")) != str(run_id)
            or str(manifest.get("prepare_run_attempt")) != str(attempt)
            or manifest.get("image") != f"maxibot-vps-api:{sha}"
            or manifest.get("platform") != "linux/amd64"
            or manifest.get("labels") != labels
            or manifest.get("archive_sha256") != digest
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(manifest.get("image_id", "")))):
        raise ReleaseAssetError("Prepared image manifest provenance or checksum mismatch")
    if manifest.get("schema") == "MAXIBOT_PREPARED_IMAGE_V2":
        try:
            from scripts.maxibot_ghcr import validate_oci_image
        except ModuleNotFoundError:
            from maxibot_ghcr import validate_oci_image
        try:
            validate_oci_image(manifest, sha, str(run_id), str(attempt))
        except ValueError as exc:
            raise ReleaseAssetError("GHCR image digest provenance mismatch") from exc
        if manifest.get("config_digest") != manifest.get("image_id"):
            raise ReleaseAssetError("GHCR config digest differs from prepared archive identity")
    if (receipt.get("schema") != "MAXIBOT_PREPARE_RECEIPT_V1"
            or receipt.get("candidate_sha") != sha
            or str(receipt.get("github_run_id")) != str(run_id)
            or str(receipt.get("github_run_attempt")) != str(attempt)
            or receipt.get("status") != "READY"
            or receipt.get("tests") != "success"
            or receipt.get("frozen_path_guard") != "success"
            or receipt.get("linux_amd64_image") != "success"
            or receipt.get("private_docker_rehearsal") != "PASS"
            or receipt.get("rehearsal_candidate_sha") != sha
            or str(receipt.get("rehearsal_prepare_run_id")) != str(run_id)
            or receipt.get("rehearsal_archive_sha256") != digest):
        raise ReleaseAssetError("Prepare receipt is incomplete or does not match this run")
    try:
        image_id, platform = inspect_prepared_archive(archive, manifest["image"], sha)
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise ReleaseAssetError("Prepared Docker archive config/platform/labels are invalid") from exc
    if image_id != manifest.get("image_id") or platform != "linux/amd64":
        raise ReleaseAssetError("Docker archive config identity, labels, or platform mismatch")
    return {"manifest": manifest, "receipt": receipt, "image_sha256": digest}


def read_bundle(payload: bytes | io.BufferedIOBase, sha: str, run_id: str | int,
                attempt: str | int) -> dict[str, bytes]:
    members = _safe_read_zip(payload, _expected_prepare_members(sha))
    validate_prepare_bundle(members, sha, run_id, attempt)
    return members


def _write_zip(target: Path, files: list[tuple[str, Path]]) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, source in files:
            if source.is_symlink() or not source.is_file():
                raise ReleaseAssetError("Release inputs must be regular files")
            archive.write(source, name)
    if target.stat().st_size <= 0 or target.stat().st_size >= MAX_RELEASE_ASSET_BYTES:
        raise ReleaseAssetError("Release asset exceeds GitHub's per-asset size limit")


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gh(*args: str) -> str:
    env = os.environ.copy()
    token = env.get("GITHUB_TOKEN", "")
    if token:
        env["GH_TOKEN"] = token
    try:
        return subprocess.check_output(["gh", *args], text=True, timeout=600, env=env).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseAssetError("GitHub Release operation failed; no retry was attempted") from exc


def _github_json(route: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ReleaseAssetError("Workflow GitHub token is unavailable for Release readback")
    import urllib.request
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = urllib.request.Request(
        f"{api}/repos/{REPOSITORY}/{route}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2026-03-10"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ReleaseAssetError("GitHub Release metadata readback was unavailable") from exc
    if not isinstance(result, dict):
        raise ReleaseAssetError("GitHub Release metadata readback was not an object")
    return result


def _publish_release(tag: str, sha: str, run_id: str, attempt: str, asset_path: Path,
                     asset_name: str, kind: str) -> dict:
    if asset_path.is_symlink() or not asset_path.is_file():
        raise ReleaseAssetError("Release asset must be a regular file")
    if asset_path.stat().st_size <= 0 or asset_path.stat().st_size >= MAX_RELEASE_ASSET_BYTES:
        raise ReleaseAssetError("Release asset exceeds GitHub's per-asset size limit")
    # Ref creation is atomic. A collision is an error; never reuse a tag/release.
    _gh("api", "--method", "POST", f"repos/{REPOSITORY}/git/refs",
        "-f", f"ref=refs/tags/{tag}", "-f", f"sha={sha}")
    _gh("release", "create", tag, "--repo", REPOSITORY, "--verify-tag", "--draft",
        "--title", tag, "--notes", f"Exact SHA {sha}; workflow run {run_id}, attempt {attempt}.")
    _gh("release", "upload", tag, "--repo", REPOSITORY, str(asset_path))
    _gh("release", "edit", tag, "--repo", REPOSITORY, "--draft=false", "--latest=false")
    # A successful publish is not READY until GitHub proves immutable readback.
    result = resolve_asset(kind, sha, run_id, attempt, asset_name, _github_json)
    local_digest = checksum(asset_path)
    if result["digest"] != f"sha256:{local_digest}":
        raise ReleaseAssetError("Published Release asset digest differs from local bytes")
    print(f"IMMUTABLE_RELEASE=PASS TAG={tag} ASSET_SHA256={local_digest}")
    return result


def _prepare_asset(sha: str, run_id: str, attempt: str, directory: Path, target: Path) -> dict:
    image_name = f"maxibot-image-{sha}.tar.gz"
    files = [(image_name, directory / image_name),
             ("maxibot-image-manifest.json", directory / "maxibot-image-manifest.json"),
             ("maxibot-prepare-result.json", directory / "maxibot-prepare-result.json")]
    validate_prepare_directory(directory, sha, run_id, attempt)
    _write_zip(target, files)
    return _publish_release(release_tag("prepare", sha, run_id, attempt), sha, run_id, attempt,
                            target, PREPARE_ASSET_NAME, "prepare")


def _evidence_asset(sha: str, prepare_run_id: str, prepare_attempt: str,
                    deploy_run_id: str, deploy_attempt: str, directory: Path,
                    result_path: Path, target: Path) -> dict:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseAssetError("Canonical Deploy result is unavailable or corrupt") from exc
    if (result.get("candidate_sha") != sha
            or str(result.get("github_run_id")) != str(deploy_run_id)
            or str(result.get("github_run_attempt", "")) != str(deploy_attempt)):
        raise ReleaseAssetError("Deploy result provenance does not match this workflow attempt")
    bundle = directory / "maxibot-image-manifest.json"
    receipt = directory / "maxibot-prepare-result.json"
    manifest_bytes = bundle.read_bytes()
    receipt_bytes = receipt.read_bytes()
    manifest = json.loads(manifest_bytes)
    validate_prepare_directory(directory, sha, prepare_run_id, prepare_attempt)
    safe_result = json.dumps(result, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    evidence_manifest = {
        "schema": "MAXIBOT_DEPLOY_EVIDENCE_V1",
        "candidate_sha": sha,
        "prepare_run_id": str(prepare_run_id),
        "prepare_run_attempt": str(prepare_attempt),
        "deploy_run_id": str(deploy_run_id),
        "deploy_run_attempt": str(deploy_attempt),
        "status": result.get("status"),
        "production_changed": result.get("production_changed"),
        "target_migration": result.get("target_migration"),
        "image_config_digest": manifest.get("image_id"),
        "image_archive_sha256": manifest.get("archive_sha256"),
        "image_platform": manifest.get("platform"),
    }
    evidence_files = {
        "result.json": safe_result,
        "image-manifest.json": manifest_bytes,
        "prepare-receipt.json": receipt_bytes,
        "evidence-manifest.json": json.dumps(
            evidence_manifest, sort_keys=True, indent=2
        ).encode("utf-8") + b"\n",
    }
    checksums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(evidence_files.items())
    ).encode("ascii")
    evidence_files["SHA256SUMS"] = checksums
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in evidence_files.items():
            archive.writestr(name, content)
    if target.stat().st_size <= 0 or target.stat().st_size >= MAX_RELEASE_ASSET_BYTES:
        raise ReleaseAssetError("Deploy evidence exceeds GitHub's per-asset size limit")
    tag = release_tag("evidence", sha, deploy_run_id, deploy_attempt)
    return _publish_release(tag, sha, deploy_run_id, deploy_attempt, target,
                            EVIDENCE_ASSET_NAME, "evidence")


def _download(repo: str, asset_id: int, token: str, target: Path, expected_size: int) -> None:
    import prepared_artifact_cache as cache
    from release_network_retry import RetryBudget
    cache.NETWORK_BUDGET = RetryBudget()
    try:
        cache.download_release(repo, asset_id, token, target, expected_size=expected_size)
    except Exception as exc:
        raise ReleaseAssetError("Immutable Release evidence download failed") from exc


def read_evidence(sha: str, deploy_run_id: str, deploy_attempt: str,
                  target_dir: Path, github_get, token: str) -> dict:
    asset = resolve_asset("evidence", sha, deploy_run_id, deploy_attempt,
                          EVIDENCE_ASSET_NAME, github_get)
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="maxibot-evidence-") as temporary:
        archive_path = Path(temporary) / EVIDENCE_ASSET_NAME
        _download(REPOSITORY, asset["id"], token, archive_path, expected_size=asset.get("size"))
        if checksum(archive_path) != asset["digest"][7:]:
            raise ReleaseAssetError("Downloaded immutable evidence digest mismatch")
        members = _safe_read_zip(archive_path, EVIDENCE_MEMBERS)
        expected_sum_lines = "".join(
            f"{hashlib.sha256(members[name]).hexdigest()}  {name}\n"
            for name in sorted(EVIDENCE_MEMBERS[:-1])
        ).encode("ascii")
        if members["SHA256SUMS"] != expected_sum_lines:
            raise ReleaseAssetError("Deploy evidence file checksums mismatch")
        manifest = _json_member(members, "evidence-manifest.json")
        result = _json_member(members, "result.json")
        if (manifest.get("schema") != "MAXIBOT_DEPLOY_EVIDENCE_V1"
                or manifest.get("candidate_sha") != sha
                or str(manifest.get("deploy_run_id")) != str(deploy_run_id)
                or str(manifest.get("deploy_run_attempt")) != str(deploy_attempt)):
            raise ReleaseAssetError("Deploy evidence manifest identity mismatch")
        if (result.get("candidate_sha") != sha
                or str(result.get("github_run_id")) != str(deploy_run_id)
                or str(result.get("github_run_attempt")) != str(deploy_attempt)
                or result.get("target_migration") != manifest.get("target_migration")):
            raise ReleaseAssetError("Canonical result identity differs from evidence manifest")
        for name, data in members.items():
            if name == "SHA256SUMS":
                continue
            output = target_dir / name
            if output.is_symlink():
                raise ReleaseAssetError("Evidence destination cannot be a symlink")
            output.write_bytes(data)
        return manifest


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    sha = os.environ.get("CANDIDATE_SHA", os.environ.get("GITHUB_SHA", ""))
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    try:
        if command == "publish-prepare":
            directory = Path(os.environ["RUNNER_TEMP"]) / "maxibot-image"
            target = Path(tempfile.mkdtemp(prefix="maxibot-release-")) / PREPARE_ASSET_NAME
            result = _prepare_asset(sha, run_id, attempt, directory, target)
            output = os.environ.get("GITHUB_OUTPUT", "")
            if output:
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(f"release_tag={release_tag('prepare', sha, run_id, attempt)}\n")
                    stream.write(f"release_asset_id={result['id']}\n")
                    stream.write(f"release_asset_sha256={result['digest'][7:]}\n")
                    stream.write(f"release_url=https://github.com/{REPOSITORY}/releases/tag/{release_tag('prepare', sha, run_id, attempt)}\n")
        elif command == "publish-evidence":
            directory = Path(os.environ["PREPARED_BUNDLE_DIRECTORY"])
            target = Path(tempfile.mkdtemp(prefix="maxibot-evidence-")) / EVIDENCE_ASSET_NAME
            _evidence_asset(
                sha, os.environ["PREPARE_RUN_ID"], os.environ["PREPARE_RUN_ATTEMPT"],
                run_id, attempt, directory, Path(os.environ["RELEASE_RESULT"]), target,
            )
        elif command == "read-evidence":
            import prepared_artifact_cache as cache
            read_evidence(sha, run_id, attempt, Path(os.environ["EVIDENCE_DESTINATION"]),
                          lambda route: cache.github(REPOSITORY, route, os.environ["GITHUB_TOKEN"], "release_evidence"),
                          os.environ["GITHUB_TOKEN"])
        else:
            raise ReleaseAssetError("Unknown Release asset command")
        return 0
    except (OSError, KeyError, ValueError, ReleaseAssetError) as exc:
        print(f"MAXIBOT_RELEASE_ASSETS=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
