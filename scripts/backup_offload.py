#!/usr/bin/env python3
"""Fail-closed backup retention/offload planner with WebDAV adapter."""
from __future__ import annotations

import argparse
import hashlib
import os
import base64
import urllib.error
import urllib.request
from pathlib import Path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class WebDavAdapter:
    """Minimal WebDAV adapter; credentials are never included in output."""

    def __init__(self) -> None:
        self.base = os.environ["MAIL_CLOUD_WEBDAV_URL"].rstrip("/")
        self.login = os.environ["MAIL_CLOUD_LOGIN"]
        self.password = os.environ["MAIL_CLOUD_APP_PASSWORD"]
        self.root = os.environ["MAIL_CLOUD_ROOT"].strip("/")

    def _request(self, method: str, url: str, data: bytes | None = None) -> urllib.response.addinfourl:
        auth = base64.b64encode(f"{self.login}:{self.password}".encode()).decode()
        request = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Basic {auth}"})
        return urllib.request.urlopen(request, timeout=30)

    def upload_and_verify(self, path: Path) -> bool:
        remote = f"{self.base}/{self.root}/{path.name}"
        with path.open("rb") as handle:
            body = handle.read()
        with self._request("PUT", remote, body) as response:
            if response.status not in (200, 201, 204):
                return False
        with self._request("HEAD", remote) as response:
            return int(response.headers.get("Content-Length", "-1")) == len(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/opt/maxibot/backups"))
    parser.add_argument("--keep", type=int, default=int(os.getenv("BACKUP_LOCAL_KEEP", "5")))
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args()
    if args.keep < 1 or not args.root.is_dir():
        print("STATUS=BLOCKED\nBLOCKER=invalid_backup_policy_or_root")
        return 2
    files = sorted((p for p in args.root.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    candidates = files[args.keep:]
    configured = all(os.getenv(name) for name in ("MAIL_CLOUD_WEBDAV_URL", "MAIL_CLOUD_LOGIN", "MAIL_CLOUD_APP_PASSWORD", "MAIL_CLOUD_ROOT"))
    print(f"BACKUPS_TOTAL={len(files)}")
    print(f"BACKUPS_KEEP={min(len(files), args.keep)}")
    print(f"BACKUPS_OFFLOAD_CANDIDATES={len(candidates)}")
    print(f"MAIL_CLOUD_CONFIGURED={'YES' if configured else 'NO'}")
    for path in candidates:
        print(f"CANDIDATE path={path} bytes={path.stat().st_size} sha256={digest(path)}")
    print("REMOTE_DELETE_REQUIRES=upload_success+remote_existence+size_hash_verification+OWNER_APPROVED")
    print("MODE=DRY_RUN")
    print("STATUS=PASS" if configured or not candidates else "STATUS=WARN")
    return 0 if configured or not candidates else 1


if __name__ == "__main__":
    raise SystemExit(main())
