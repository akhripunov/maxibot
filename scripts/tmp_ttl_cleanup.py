#!/usr/bin/env python3
"""Report stale temporary files without touching active runtime artifacts."""
from __future__ import annotations

import argparse
import os
import stat
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/tmp"))
    parser.add_argument("--ttl-hours", type=int, default=int(os.getenv("TMP_TTL_HOURS", "168")))
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args()
    if args.ttl_hours < 1:
        print("STATUS=BLOCKED\nBLOCKER=ttl_must_be_positive")
        return 2
    if not args.root.is_dir():
        print("STATUS=BLOCKED\nBLOCKER=tmp_root_missing")
        return 2
    cutoff = time.time() - args.ttl_hours * 3600
    candidates = []
    for path in args.root.iterdir():
        try:
            info = path.lstat()
        except OSError:
            continue
        if info.st_mtime >= cutoff or stat.S_ISSOCK(info.st_mode) or stat.S_ISFIFO(info.st_mode):
            continue
        if path.name.endswith((".lock", ".pid", ".sock")) or path.name.startswith(("current-", "active-")):
            continue
        candidates.append((path, info.st_size if stat.S_ISREG(info.st_mode) else 0))
    print(f"TMP_ROOT={args.root}")
    print(f"TMP_TTL_HOURS={args.ttl_hours}")
    print(f"TMP_DELETE_CANDIDATES={len(candidates)}")
    print(f"RECLAIMABLE_BYTES={sum(size for _, size in candidates)}")
    for path, size in sorted(candidates, key=lambda item: str(item[0]))[:200]:
        print(f"CANDIDATE path={path} bytes={size}")
    print("MODE=DRY_RUN")
    print("STATUS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
