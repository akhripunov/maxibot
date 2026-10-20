"""MAXIBOT's digest-pinned GHCR release receipts and deployment evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

try:
    from scripts.release_network_retry import (NetworkUnavailable, RetryBudget,
        is_retryable_network_error, retry_network_read)
except ModuleNotFoundError:  # Direct script execution puts scripts/ on sys.path.
    from release_network_retry import (NetworkUnavailable, RetryBudget,
        is_retryable_network_error, retry_network_read)


REPOSITORY = "akhripunov/maxibot"
IMAGE_REPOSITORY = "ghcr.io/akhripunov/maxibot-vps-api"
# Reuse the already-linked image package so GITHUB_TOKEN's packages:write grant
# covers all V5 artifacts without requiring per-package Actions access setup.
PREPARE_REPOSITORY = IMAGE_REPOSITORY
EVIDENCE_REPOSITORY = IMAGE_REPOSITORY
PREPARE_ARTIFACT_TYPE = "application/vnd.maxibot.prepare.receipt.v1"
PREPARE_LAYER_MEDIA_TYPE = PREPARE_ARTIFACT_TYPE + "+json"
EVIDENCE_ARTIFACT_TYPE = "application/vnd.maxibot.deploy.evidence.v1"
EVIDENCE_LAYER_MEDIA_TYPE = EVIDENCE_ARTIFACT_TYPE + "+json"
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_EMPTY_CONFIG_MEDIA_TYPE = "application/vnd.oci.empty.v1+json"
OCI_EMPTY_CONFIG_DIGEST = "sha256:" + hashlib.sha256(b"{}").hexdigest()
OCI_ACCEPT = ", ".join((OCI_MANIFEST_MEDIA_TYPE,
                         "application/vnd.docker.distribution.manifest.v2+json"))
SHA40 = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
NETWORK_BUDGET = RetryBudget()


class ArtifactError(ValueError):
    """An OCI release artifact is missing, unsafe, or not bound to its run."""


class MissingArtifactError(ArtifactError):
    """The exact immutable GHCR tag does not exist."""


def _header_value(headers: dict[str, str], name: str) -> str:
    """Read an HTTP header without relying on the spelling/case used by urllib."""
    expected = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == expected), "")


def artifact_tag(kind: str, sha: str, run_id: str | int, attempt: str | int) -> str:
    if kind not in {"prepare", "evidence"} or not SHA40.fullmatch(sha):
        raise ArtifactError("Invalid MAXIBOT artifact identity")
    run, try_number = str(run_id), str(attempt)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run) or not re.fullmatch(r"[1-9][0-9]{0,5}", try_number):
        raise ArtifactError("Invalid MAXIBOT workflow run or attempt")
    return f"{kind}-{sha}-{run}-{try_number}"


def verify_oci_artifact(manifest_bytes: bytes, expected_digest: str, payload: bytes,
                        artifact_type: str, layer_media_type: str) -> bytes:
    if not DIGEST.fullmatch(expected_digest):
        raise ArtifactError("GHCR did not return a valid immutable OCI digest")
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_digest[7:]:
        raise ArtifactError("GHCR artifact manifest digest mismatch")
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ArtifactError("GHCR release artifact exceeds its size limit")
    try:
        manifest = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ArtifactError("GHCR artifact manifest is invalid JSON") from exc
    config = manifest.get("config") if isinstance(manifest, dict) else None
    layers = manifest.get("layers") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE
            or manifest.get("artifactType") != artifact_type
            or not isinstance(config, dict)
            or config.get("mediaType") != OCI_EMPTY_CONFIG_MEDIA_TYPE
            or config.get("digest") != OCI_EMPTY_CONFIG_DIGEST or config.get("size") != 2
            or not isinstance(layers, list) or len(layers) != 1):
        raise ArtifactError("GHCR artifact type or OCI layout is invalid")
    layer = layers[0]
    layer_digest = layer.get("digest") if isinstance(layer, dict) else None
    layer_size = layer.get("size") if isinstance(layer, dict) else None
    if (not isinstance(layer, dict) or layer.get("mediaType") != layer_media_type
            or not isinstance(layer_digest, str) or not DIGEST.fullmatch(layer_digest)
            or not isinstance(layer_size, int) or layer_size != len(payload)
            or hashlib.sha256(payload).hexdigest() != layer_digest[7:]):
        raise ArtifactError("GHCR artifact layer digest, size, or type mismatch")
    return payload


def validate_prepare_receipt(receipt: dict, sha: str, run_id: str | int,
                             attempt: str | int) -> dict:
    image = receipt.get("image") if isinstance(receipt, dict) else None
    if (not isinstance(receipt, dict) or receipt.get("schema") != "MAXIBOT_PREPARE_RECEIPT_V2"
            or receipt.get("candidate_sha") != sha
            or str(receipt.get("github_run_id")) != str(run_id)
            or str(receipt.get("github_run_attempt")) != str(attempt)
            or receipt.get("status") != "READY" or not isinstance(image, dict)
            or image.get("repository") != IMAGE_REPOSITORY
            or image.get("tag") != artifact_tag("prepare", sha, run_id, attempt)
            or not isinstance(image.get("digest"), str) or not DIGEST.fullmatch(image["digest"])
            or image.get("platform") != "linux/amd64"
            or not isinstance(image.get("config_digest"), str)
            or not DIGEST.fullmatch(image["config_digest"])
            or image.get("labels") != {
                "org.opencontainers.image.revision": sha,
                "org.opencontainers.image.source-sha": sha,
                "org.maxibot.release.sha": sha,
            }
            or receipt.get("tests") != "success"
            or receipt.get("frozen_path_guard") != "success"
            or receipt.get("linux_amd64_image") != "success"
            or receipt.get("ghcr_image_publish") != "success"
            or receipt.get("private_docker_rehearsal") != "PASS"):
        raise ArtifactError("Prepare receipt does not prove the exact MAXIBOT OCI image")
    return receipt


def resolve_oci_artifact(repository: str, tag: str, artifact_type: str,
                         layer_media_type: str,
                         fetch: Callable[[str, str], tuple[bytes, dict[str, str]]]) -> tuple[str, bytes]:
    """Resolve a unique registry tag, then use only a digest-verified readback."""
    if repository != IMAGE_REPOSITORY:
        raise ArtifactError("Unapproved MAXIBOT artifact repository")
    if not re.fullmatch(r"(?:prepare|evidence)-[0-9a-f]{40}-[1-9][0-9]{0,19}-[1-9][0-9]{0,5}", tag):
        raise ArtifactError("Invalid MAXIBOT artifact tag")
    base = f"https://ghcr.io/v2/{repository.split('/', 1)[1]}"
    tag_manifest, tag_headers = fetch(f"{base}/manifests/{tag}", OCI_ACCEPT)
    digest = _header_value(tag_headers, "Docker-Content-Digest")
    if not DIGEST.fullmatch(digest) or hashlib.sha256(tag_manifest).hexdigest() != digest[7:]:
        raise ArtifactError("GHCR tag did not resolve to a verified manifest digest")
    manifest, headers = fetch(f"{base}/manifests/{digest}", OCI_ACCEPT)
    if _header_value(headers, "Docker-Content-Digest") != digest or manifest != tag_manifest:
        raise ArtifactError("GHCR artifact changed between tag and digest read")
    try:
        parsed = json.loads(manifest)
        layer = parsed["layers"][0]
        blob_digest = layer["digest"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ArtifactError("GHCR artifact manifest is incomplete") from exc
    if not isinstance(blob_digest, str) or not DIGEST.fullmatch(blob_digest):
        raise ArtifactError("GHCR artifact layer digest is invalid")
    payload, _ = fetch(f"{base}/blobs/{blob_digest}", "application/octet-stream")
    verified = verify_oci_artifact(manifest, digest, payload, artifact_type, layer_media_type)
    return digest, verified


def _network_failure_category(error: BaseException) -> str:
    import urllib.error
    if isinstance(error, NetworkUnavailable):
        return "NETWORK_UNAVAILABLE"
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    return type(reason).__name__


def _retry_registry_read(operation: Callable, stage: str, budget: RetryBudget | None = None):
    try:
        return retry_network_read(operation, stage, is_retryable_network_error,
                                  budget=budget or NETWORK_BUDGET)
    except NetworkUnavailable:
        raise ArtifactError(f"GHCR {stage} failed (NETWORK_UNAVAILABLE)") from None


def _registry_fetch(repository: str, username: str, password: str, *,
                    budget: RetryBudget | None = None, opener=None):
    """Build a scoped GHCR reader. Basic credentials go only to ghcr.io/token."""
    import base64
    import urllib.parse
    import urllib.request
    request_opener = opener or urllib.request.urlopen
    retry_budget = budget or NETWORK_BUDGET

    if repository != IMAGE_REPOSITORY or not username or not password:
        raise ArtifactError("Scoped MAXIBOT GHCR credentials are unavailable")
    repository_path = repository.split("/", 1)[1]
    realm = f"https://ghcr.io/token?{urllib.parse.urlencode({'service': 'ghcr.io', 'scope': 'repository:' + repository_path + ':pull'})}"
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    bearer = None

    def fetch(url: str, accept: str) -> tuple[bytes, dict[str, str]]:
        nonlocal bearer
        if not url.startswith(f"https://ghcr.io/v2/{repository_path}/"):
            raise ArtifactError("GHCR request escaped the approved MAXIBOT package")
        if bearer is None:
            request = urllib.request.Request(realm, headers={"Authorization": f"Basic {auth}",
                "Accept": "application/json"})
            try:
                def fetch_token():
                    with request_opener(request, timeout=30) as response:
                        return json.loads(response.read(65536).decode("utf-8"))
                token_body = _retry_registry_read(fetch_token, "pull_token", retry_budget)
            except urllib.error.HTTPError as exc:
                raise ArtifactError(f"GHCR pull token request failed ({_network_failure_category(exc)})") from None
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError,
                    UnicodeDecodeError) as exc:
                raise ArtifactError(
                    f"GHCR pull token request failed ({_network_failure_category(exc)})") from None
            bearer = token_body.get("token") or token_body.get("access_token") if isinstance(token_body, dict) else None
            if not isinstance(bearer, str) or not bearer or len(bearer) > 8192:
                raise ArtifactError("GHCR returned an invalid pull token")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}",
            "Accept": accept, "User-Agent": "maxibot-release-orchestrator"})
        try:
            def fetch_artifact():
                with request_opener(request, timeout=30) as response:
                    body = response.read(MAX_ARTIFACT_BYTES + 2)
                    if len(body) > MAX_ARTIFACT_BYTES:
                        raise ArtifactError("GHCR artifact response exceeds its size limit")
                    return body, {key: value for key, value in response.headers.items()}
            return _retry_registry_read(fetch_artifact, "digest_readback", retry_budget)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and "/manifests/" in url and not DIGEST.fullmatch(url.rsplit("/", 1)[-1]):
                raise MissingArtifactError("GHCR artifact tag is not published") from None
            raise ArtifactError(f"GHCR digest readback failed ({_network_failure_category(exc)})") from None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError,
                UnicodeDecodeError) as exc:
            raise ArtifactError(
                f"GHCR digest readback failed ({_network_failure_category(exc)})") from None
    return fetch


def registry_pull_token(repository: str, username: str, password: str, *,
                        budget: RetryBudget | None = None, opener=None) -> str:
    """Exchange a workflow token for a short-lived, repository-pull-only GHCR token."""
    import urllib.parse
    import urllib.request
    request_opener = opener or urllib.request.urlopen

    if repository != IMAGE_REPOSITORY or not username or not password:
        raise ArtifactError("Only the approved MAXIBOT image repository may receive a pull token")
    query = urllib.parse.urlencode({"service": "ghcr.io",
        "scope": f"repository:{repository.split('/', 1)[1]}:pull"})
    request = urllib.request.Request(f"https://ghcr.io/token?{query}", headers={
        "Authorization": "Basic " + __import__("base64").b64encode(
            f"{username}:{password}".encode()).decode(), "Accept": "application/json"})
    try:
        def fetch_token():
            with request_opener(request, timeout=20) as response:
                return json.loads(response.read(16384).decode("utf-8"))
        body = _retry_registry_read(fetch_token, "pull_token", budget)
    except urllib.error.HTTPError as exc:
        raise ArtifactError(
            f"Could not mint a scoped MAXIBOT GHCR pull token ({_network_failure_category(exc)})") from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError,
            UnicodeDecodeError) as exc:
        raise ArtifactError(
            f"Could not mint a scoped MAXIBOT GHCR pull token ({_network_failure_category(exc)})") from None
    token = (body.get("token") or body.get("access_token")) if isinstance(body, dict) else None
    # Registry V2 defines a 60-second lifetime when expires_in is omitted.
    # GHCR returns this token-only response; explicit invalid lifetimes still fail closed.
    # https://docs.docker.com/reference/api/registry/auth/#expires_in
    expires = body.get("expires_in", 60) if isinstance(body, dict) else 0
    if not isinstance(token, str) or not token or len(token) > 8192 or type(expires) is not int or not 0 < expires <= 3600:
        raise ArtifactError("GHCR did not return a short-lived pull-only bearer token")
    return token


def main_pull_token() -> int:
    """Print only the short-lived pull-only token for local shell pipe capture."""
    from contextlib import redirect_stdout
    try:
        # Retry telemetry must never become part of the secret stdin payload.
        with redirect_stdout(sys.stderr):
            token = registry_pull_token(IMAGE_REPOSITORY, os.environ.get("GITHUB_ACTOR", ""),
                                        os.environ.get("GITHUB_TOKEN", ""))
        sys.stdout.write(token)
        return 0
    except ArtifactError as exc:
        print(f"MAXIBOT_GHCR_PULL_TOKEN=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


def publish_oci_artifact(repository: str, kind: str, sha: str, run_id: str | int,
                         attempt: str | int, payload_path, artifact_type: str,
                         layer_media_type: str, *, run: Callable = None,
                         fetch_factory: Callable = _registry_fetch) -> tuple[str, bytes]:
    """Publish one run-unique OCI artifact and require immediate digest readback."""
    from pathlib import Path
    import os

    if repository not in {PREPARE_REPOSITORY, EVIDENCE_REPOSITORY}:
        raise ArtifactError("Unapproved MAXIBOT artifact repository")
    tag = artifact_tag(kind, sha, run_id, attempt)
    path = Path(payload_path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES:
        raise ArtifactError("MAXIBOT OCI artifact payload must be a bounded regular file")
    env = os.environ.copy()
    username = env.get("GHCR_READ_USERNAME") or env.get("GITHUB_ACTOR", "")
    password = env.get("GHCR_READ_TOKEN") or env.get("GITHUB_TOKEN", "")
    if not username or not password:
        raise ArtifactError("Scoped MAXIBOT GHCR write credentials are unavailable")
    runner = run or _run_oras
    auth_dir = Path(tempfile.mkdtemp(prefix="maxibot-ghcr-auth-"))
    auth_file = auth_dir / "config.json"
    auth_file.write_text("{}\n", encoding="utf-8")
    auth_file.chmod(0o600)
    try:
        login = ["oras", "login", "--registry-config", str(auth_file), "ghcr.io",
                 "--username", username, "--password-stdin"]
        runner(login, input=password)
        command = ["oras", "push", "--debug", "--disable-path-validation",
                   "--registry-config", str(auth_file), "--format", "json",
                   "--artifact-type", artifact_type,
                   "--annotation", "org.opencontainers.image.source=https://github.com/akhripunov/maxibot",
                   f"{repository}:{tag}",
                   f"{path}:{layer_media_type}"]
        try:
            response = json.loads(runner(command))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ArtifactError("ORAS did not return valid publication metadata") from exc
    finally:
        shutil.rmtree(auth_dir, ignore_errors=True)
    digest = response.get("digest") if isinstance(response, dict) else None
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ArtifactError("ORAS did not return one exact artifact digest")
    actual_digest, payload = resolve_oci_artifact(repository, tag, artifact_type,
        layer_media_type, fetch_factory(repository, username, password))
    expected = path.read_bytes()
    if actual_digest != digest or payload != expected:
        raise ArtifactError("Published GHCR artifact differs from exact local bytes")
    return digest, payload


def _run_oras(args, input=None):
    try:
        result = subprocess.run(args, input=input, text=True, capture_output=True,
                                check=True, timeout=300, env=os.environ.copy())
    except subprocess.CalledProcessError as exc:
        # Only emit a small, aggressively redacted suffix. These diagnostics may
        # appear in public Actions logs, so never include commands or exceptions.
        detail = "\n".join(str(value) for value in (exc.stderr, exc.stdout) if value).lower()
        status = re.search(r"\b(401|403|404|405|406|409|413|415|422|429|500|502|503)\b", detail)
        if status:
            category = "HTTP " + status.group(1)
        elif "unauthorized" in detail or "authentication required" in detail:
            category = "unauthorized"
        elif "denied" in detail or "forbidden" in detail:
            category = "permission denied"
        elif "unsupported" in detail:
            category = "unsupported request"
        else:
            category = "registry request rejected"
        operation = args[1] if len(args) > 1 and args[1] in {"login", "push"} else "operation"
        diagnostic = _redacted_oras_diagnostic(
            "\n".join(str(value) for value in (exc.stderr, exc.stdout) if value),
            secrets=[input, os.environ.get("GITHUB_TOKEN"), os.environ.get("GHCR_READ_TOKEN")],
        )
        suffix = f"; detail: {diagnostic}" if diagnostic else ""
        raise ArtifactError(f"ORAS {operation} failed (exit {exc.returncode}; {category}{suffix})") from None
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArtifactError("ORAS OCI publication failed") from exc
    return result.stdout


def _redacted_oras_diagnostic(value: str, secrets=()) -> str:
    """Return at most 240 chars of low-risk registry text for public CI logs."""
    text = str(value or "")
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    patterns = (
        (r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]"),
        (r"(?i)\bBasic\s+[^\s,;]+", "Basic [REDACTED]"),
        (r"(?i)\b(?:token|password|secret|authorization)(\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)\b(?:registry\s+)?(?:token|password|secret|authorization)\s+[^\s,;]+(?:\s+[^\s,;]+)?", "[REDACTED]"),
        (r"(?i)\b(?:secret|token|password)[-_][A-Za-z0-9_-]{4,}\b", "[REDACTED]"),
        (r"https?://[^\s/@]+:[^\s/@]+@", "https://[REDACTED]@"),
        (r"https?://[^\s?#]+\?[^\s#]*", "[URL WITH QUERY REDACTED]"),
        (r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b", "[REDACTED]"),
        (r"\b[A-Za-z0-9_/-]{32,}\b", "[REDACTED]"),
        (r"\{[^{}]*\}", "[RESPONSE BODY REDACTED]"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    # Keep the tail where ORAS commonly places the registry's actionable reason.
    return text[-240:]


def publish_prepare_receipt(sha: str, run_id: str, attempt: str,
                            receipt_path, image_manifest_path, output_path) -> str:
    from pathlib import Path
    receipt_file, image_file = Path(receipt_path), Path(image_manifest_path)
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        image_manifest = json.loads(image_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError("Exact Prepare receipt or image manifest is unavailable") from exc
    image = image_manifest.get("image")
    receipt["schema"] = "MAXIBOT_PREPARE_RECEIPT_V2"
    receipt["image"] = image
    validate_prepare_receipt(receipt, sha, run_id, attempt)
    receipt_file.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    digest, _ = publish_oci_artifact(PREPARE_REPOSITORY, "prepare", sha, run_id, attempt,
        receipt_file, PREPARE_ARTIFACT_TYPE, PREPARE_LAYER_MEDIA_TYPE)
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"MAXIBOT_PREPARE_RECEIPT_DIGEST={digest}\n")
        output.write(f"MAXIBOT_PREPARE_RECEIPT_TAG={artifact_tag('prepare', sha, run_id, attempt)}\n")
    print(f"MAXIBOT_GHCR_PREPARE_RECEIPT=PASS DIGEST={digest}")
    return digest


def publish_deploy_evidence(sha: str, run_id: str, attempt: str, evidence_path,
                            output_path) -> str:
    from pathlib import Path
    path = Path(evidence_path)
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError("Canonical MAXIBOT Deploy evidence is unavailable") from exc
    if (evidence.get("schema") != "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1"
            or evidence.get("candidate_sha") != sha
            or str(evidence.get("github_run_id")) != str(run_id)
            or str(evidence.get("github_run_attempt")) != str(attempt)):
        raise ArtifactError("Deploy evidence does not bind the exact workflow attempt")
    digest, payload = publish_oci_artifact(EVIDENCE_REPOSITORY, "evidence", sha, run_id, attempt,
        path, EVIDENCE_ARTIFACT_TYPE, EVIDENCE_LAYER_MEDIA_TYPE)
    if payload != path.read_bytes():
        raise ArtifactError("Published MAXIBOT Deploy evidence differs from exact local bytes")
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"digest={digest}\n")
        output.write(f"MAXIBOT_DEPLOY_EVIDENCE_DIGEST={digest}\n")
        output.write(f"MAXIBOT_DEPLOY_EVIDENCE_TAG={artifact_tag('evidence', sha, run_id, attempt)}\n")
    print(f"MAXIBOT_GHCR_DEPLOY_EVIDENCE=PASS DIGEST={digest}")
    return digest


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("publish-prepare", "publish-evidence", "read-evidence", "mint-pull-token"))
    args = parser.parse_args()
    try:
        if args.operation == "mint-pull-token":
            return main_pull_token()
        sha, run_id = os.environ["CANDIDATE_SHA"], os.environ["GITHUB_RUN_ID"]
        attempt, output = os.environ["GITHUB_RUN_ATTEMPT"], os.environ["GITHUB_OUTPUT"]
        if args.operation == "publish-prepare":
            publish_prepare_receipt(sha, run_id, attempt,
                os.environ["PREPARE_RECEIPT"], os.environ["IMAGE_MANIFEST"], output)
        elif args.operation == "publish-evidence":
            publish_deploy_evidence(sha, run_id, attempt, os.environ["RELEASE_RESULT"], output)
        else:
            digest, payload = resolve_oci_artifact(EVIDENCE_REPOSITORY,
                artifact_tag("evidence", sha, run_id, attempt), EVIDENCE_ARTIFACT_TYPE,
                EVIDENCE_LAYER_MEDIA_TYPE,
                _registry_fetch(EVIDENCE_REPOSITORY, os.environ["GITHUB_ACTOR"], os.environ["GITHUB_TOKEN"]))
            if digest != os.environ["DEPLOY_EVIDENCE_DIGEST"]:
                raise ArtifactError("GHCR Deploy evidence differs from the exact publisher digest")
            evidence = json.loads(payload.decode("utf-8"))
            if (evidence.get("schema") != "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1"
                    or evidence.get("candidate_sha") != sha
                    or str(evidence.get("github_run_id")) != run_id
                    or str(evidence.get("github_run_attempt")) != attempt):
                raise ArtifactError("GHCR Deploy evidence identity does not match this workflow attempt")
            target = Path(os.environ["EVIDENCE_DESTINATION"]) / "result.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            print(f"MAXIBOT_GHCR_EVIDENCE_READBACK=PASS DIGEST={digest}")
        return 0
    except (KeyError, OSError, ArtifactError) as exc:
        print(f"MAXIBOT_GHCR_ARTIFACT=BLOCKED REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
