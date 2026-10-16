"""Exact-digest GHCR proof and VPS pull commands for MAXIBOT releases."""
from __future__ import annotations

import base64
from contextlib import nullcontext
import json
import re
import urllib.error
import urllib.parse
import urllib.request

SHA40 = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REPOSITORY = "ghcr.io/akhripunov/maxibot-vps-api"


def verify_ghcr_digest_access(repository: str, digest: str, username: str, token: str,
                              opener=None) -> str:
    """Prove read access to the exact immutable manifest without pulling layers."""
    if repository != REPOSITORY or not DIGEST.fullmatch(digest or ""):
        raise RuntimeError("GHCR exact-digest read check failed")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username or "") or not token:
        raise RuntimeError("GHCR exact-digest read check failed")
    path = repository.removeprefix("ghcr.io/")
    url = f"https://ghcr.io/v2/{path}/manifests/{digest}"
    credential = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
    headers = {
        "Accept": ", ".join((
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        )),
    }
    def open_no_redirect(request, timeout):
        class RejectRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None
        return urllib.request.build_opener(RejectRedirects).open(request, timeout=timeout)
    selected_opener = opener or open_no_redirect

    def open_response(request):
        try:
            return selected_opener(request, timeout=10)
        except urllib.error.HTTPError as error:
            return error

    def response_context(response):
        return nullcontext(response) if isinstance(response, urllib.error.HTTPError) else response

    try:
        anonymous = urllib.request.Request(url, headers=headers)
        with response_context(open_response(anonymous)) as response:
            status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
            if status == 200:
                if (response.geturl() != url
                        or response.headers.get("Docker-Content-Digest") != digest):
                    raise RuntimeError("GHCR exact-digest read check failed")
                return "PASS"
            challenge = response.headers.get("WWW-Authenticate", "") if status == 401 else ""
        if not challenge.startswith("Bearer "):
            raise RuntimeError("GHCR exact-digest read check failed")
        attributes = dict(re.findall(r'([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"', challenge[7:]))
        expected_scope = f"repository:{path}:pull"
        if (attributes.get("realm") != "https://ghcr.io/token"
                or attributes.get("service") != "ghcr.io"
                or attributes.get("scope") != expected_scope):
            raise RuntimeError("GHCR exact-digest read check failed")
        token_url = attributes["realm"] + "?" + urllib.parse.urlencode({
            "service": attributes["service"], "scope": expected_scope,
        })
        token_request = urllib.request.Request(token_url, headers={
            "Authorization": f"Basic {credential}", "Accept": "application/json",
        })
        with response_context(open_response(token_request)) as response:
            status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
            if status != 200 or response.geturl() != token_url:
                raise RuntimeError("GHCR exact-digest read check failed")
            raw_payload = response.read(65537)
            if len(raw_payload) > 65536:
                raise RuntimeError("GHCR exact-digest read check failed")
        payload = json.loads(raw_payload)
        bearer = payload.get("token") or payload.get("access_token") if isinstance(payload, dict) else ""
        if not isinstance(bearer, str) or not re.fullmatch(r"[^\s\r\n]{1,16384}", bearer):
            raise RuntimeError("GHCR exact-digest read check failed")
        manifest_request = urllib.request.Request(url, headers={
            **headers, "Authorization": f"Bearer {bearer}",
        })
        with response_context(open_response(manifest_request)) as response:
            status = response.code if isinstance(response, urllib.error.HTTPError) else response.status
            if (status != 200 or response.geturl() != url
                    or response.headers.get("Docker-Content-Digest") != digest):
                raise RuntimeError("GHCR exact-digest read check failed")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, TypeError):
        raise RuntimeError("GHCR exact-digest read check failed") from None
    return "PASS"


def validate_oci_image(proof: dict, candidate: str, run_id: str, attempt: str) -> dict:
    if (not SHA40.fullmatch(candidate or "")
            or proof.get("schema") != "MAXIBOT_PREPARED_IMAGE_V2"
            or proof.get("candidate_sha") != candidate
            or str(proof.get("prepare_run_id")) != str(run_id)
            or str(proof.get("prepare_run_attempt")) != str(attempt)
            or proof.get("source_sha") != candidate):
        raise ValueError("OCI image identity or Prepare attempt mismatch")
    if proof.get("repository") != REPOSITORY:
        raise ValueError("OCI repository mismatch")
    if not DIGEST.fullmatch(proof.get("manifest_digest", "")) or not DIGEST.fullmatch(proof.get("config_digest", "")):
        raise ValueError("OCI manifest/config digest is invalid")
    if proof.get("platform") != "linux/amd64":
        raise ValueError("OCI platform is not linux/amd64")
    expected_labels = {"org.opencontainers.image.revision": candidate,
                       "org.opencontainers.image.source-sha": candidate,
                       "org.maxibot.release.sha": candidate}
    if any((proof.get("labels") or {}).get(key) != value for key, value in expected_labels.items()):
        raise ValueError("OCI source labels do not match the candidate")
    return {**proof, "reference": f"{REPOSITORY}@{proof['manifest_digest']}"}


def select_image_transport(manifest: dict, requested: str | None) -> str:
    """Choose the receipt transport, allowing only an explicit verified archive fallback."""
    prepared = manifest.get("transport", "archive")
    if prepared not in {"archive", "ghcr-oci"}:
        raise ValueError("Prepared image transport is invalid")
    if requested in (None, "", "prepared"):
        return prepared
    if requested == prepared:
        return prepared
    if requested == "archive" and prepared == "ghcr-oci":
        if not DIGEST.fullmatch(manifest.get("image_id", "")):
            raise ValueError("Verified archive fallback config digest is unavailable")
        if not re.fullmatch(r"[a-f0-9]{64}", manifest.get("archive_sha256", "")):
            raise ValueError("Verified archive fallback checksum is unavailable")
        return "archive"
    raise ValueError("Requested image transport is not proven by the Prepare receipt")


def validate_rollback_image_identity(container_image_id: str, tag_image_id: str,
                                    platform: str, repo_digests: list[str]) -> dict:
    """Prove the currently running rollback image is retained by its local tag."""
    if not DIGEST.fullmatch(container_image_id or "") or not DIGEST.fullmatch(tag_image_id or ""):
        raise ValueError("Rollback image config digest is invalid")
    if container_image_id != tag_image_id:
        raise ValueError("Rollback image tag does not resolve to the running config digest")
    if platform != "linux/amd64":
        raise ValueError("Rollback image platform is not linux/amd64")
    if not isinstance(repo_digests, list):
        raise ValueError("Rollback image RepoDigest list is invalid")
    digest_pattern = re.compile(r"^[A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64}$")
    if any(not isinstance(value, str) or not digest_pattern.fullmatch(value)
           for value in repo_digests):
        raise ValueError("Rollback image RepoDigest is invalid")
    digests = sorted(set(repo_digests))
    return {
        "image_id": container_image_id,
        "repo_digests": digests,
        "rollback_identity": "OCI_DIGEST_AND_CONFIG" if digests else "LOCAL_CONFIG_DIGEST",
    }


def create_vps_pull_script(proof: dict, candidate: str, username: str = "akhripunov") -> str:
    validated = validate_oci_image(proof, candidate,
                                   str(proof.get("prepare_run_id", "")),
                                   str(proof.get("prepare_run_attempt", "")))
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
        raise ValueError("GHCR username is invalid")
    reference = validated["reference"]
    image = f"maxibot-vps-api:{candidate}"
    config = validated["config_digest"]
    return "\n".join((
        "set -eu", "set +x",
        "docker_config=$(mktemp -d /tmp/maxibot-ghcr-auth.XXXXXX)",
        'chmod 700 "$docker_config"', 'export DOCKER_CONFIG="$docker_config"',
        "trap 'docker logout ghcr.io >/dev/null 2>&1 || true; rm -rf \"$docker_config\"' EXIT",
        f"docker login ghcr.io --username '{username}' --password-stdin",
        f"docker pull '{reference}'",
        f"test \"$(docker image inspect '{reference}' --format '{{{{.Id}}}}')\" = '{config}'",
        f"test \"$(docker image inspect '{reference}' --format '{{{{.Os}}}}/{{{{.Architecture}}}}')\" = 'linux/amd64'",
        f"test \"$(docker image inspect '{reference}' --format '{{{{index .Config.Labels \"org.opencontainers.image.revision\"}}}}')\" = '{candidate}'",
        f"test \"$(docker image inspect '{reference}' --format '{{{{index .Config.Labels \"org.opencontainers.image.source-sha\"}}}}')\" = '{candidate}'",
        f"test \"$(docker image inspect '{reference}' --format '{{{{index .Config.Labels \"org.maxibot.release.sha\"}}}}')\" = '{candidate}'",
        f"docker image inspect '{reference}' --format '{{{{range .RepoDigests}}}}{{{{println .}}}}{{{{end}}}}' | grep -Fx '{reference}'",
        f"docker image tag '{reference}' '{image}'",
        f"test \"$(docker image inspect '{image}' --format '{{{{.Id}}}}')\" = '{config}'",
        "echo MAXIBOT_GHCR_IMAGE_VERIFIED=YES",
    ))
