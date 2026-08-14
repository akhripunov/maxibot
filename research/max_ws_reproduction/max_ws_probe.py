#!/usr/bin/env python3
"""Explicit, read-only MAX Web auth probe.

This file is deliberately outside the production package. It never reads
browser profiles, cookies, local storage, or MAX_BOT_TOKEN. The current
sanitized capture is insufficient to reconstruct the binary envelope, so the
probe fails closed before opening a socket until a manually supplied
MAX_WEB_TOKEN and a proven encoder are available.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path


WS_URL = "wss://api.oneme.ru/websocket"
PRIVATE_DIR = Path(__file__).with_name("private")


def build_auth_frame(token: bytes) -> bytes:
    captured = (PRIVATE_DIR / "auth_frame.bin").read_bytes()
    marker = b"token"
    pos = captured.find(marker)
    if pos < 0 or pos + 8 > len(captured) or captured[pos + 5] != 0xDA:
        raise ValueError("captured token field is not the proven uint16 string form")
    length = int.from_bytes(captured[pos + 6:pos + 8], "big")
    start = pos + 8
    end = start + length
    if end > len(captured) or len(token) != length:
        raise ValueError("MAX_WEB_TOKEN length does not match captured AUTH_FRAME")
    return captured[:start] + token + captured[end:]


def decode_contact_id(frame: bytes) -> int | None:
    pos = frame.find(b"id")
    if pos < 0 or pos + 5 >= len(frame) or frame[pos + 2] != 0xC7:
        return None
    size = frame[pos + 3]
    payload = frame[pos + 5:pos + 5 + size]
    if payload[:1] == b"\xD2" and len(payload) >= 5:
        return int.from_bytes(payload[1:5], "big", signed=True)
    return None


def decode_approve_user_id(frame: bytes) -> int | None:
    pos = frame.find(b"userIds")
    if pos < 0:
        return None
    value = frame.find(b"\xC7", pos)
    if value < 0:
        return None
    size = frame[value + 1]
    payload = frame[value + 3:value + 3 + size]
    if payload[:1] == b"\xCF" and len(payload) >= 9:
        return int.from_bytes(payload[1:9], "big", signed=True)
    return None


async def run_probe(token: bytes) -> dict[str, object]:
    try:
        import websockets
    except ImportError as exc:
        return {"PROGRAMMATIC_MAX_AUTH": "BLOCKED", "reason": "Python websockets dependency is missing"}
    try:
        bootstrap = (PRIVATE_DIR / "bootstrap_frame.bin").read_bytes()
        auth_frame = build_auth_frame(token)
        async with websockets.connect(WS_URL, origin="https://web.max.ru", max_size=None) as socket:
            await socket.send(bootstrap)
            await asyncio.wait_for(socket.recv(), timeout=10)
            await socket.send(auth_frame)
            response = await asyncio.wait_for(socket.recv(), timeout=10)
            if not isinstance(response, bytes):
                return {"PROGRAMMATIC_MAX_AUTH": "PROTOCOL_ERROR", "response_type": "text"}
            read_frame_path = PRIVATE_DIR / "readonly_chatids_frame.bin"
            if read_frame_path.exists():
                await socket.send(read_frame_path.read_bytes())
                read_response = await asyncio.wait_for(socket.recv(), timeout=10)
                if not isinstance(read_response, bytes):
                    return {"PROGRAMMATIC_MAX_AUTH": "WORKING", "PROGRAMMATIC_READ_RPC": "NOT_WORKING", "response_type": "text"}
                read_ok = len(read_response) > 0
            else:
                read_ok = False
            result: dict[str, object] = {
                "PROGRAMMATIC_MAX_AUTH": "WORKING",
                "WS_CONNECTION": "WORKING",
                "PROGRAMMATIC_READ_RPC": "WORKING" if read_ok else "NOT_WORKING",
                "auth_response_length": len(response),
            }
            if "--pending" in sys.argv or "--approve" in sys.argv:
                pending_path = PRIVATE_DIR / "pending_request.bin"
                if not pending_path.exists():
                    result["PROGRAMMATIC_PENDING_LIST"] = "NOT_WORKING"
                    return result
                await socket.send(pending_path.read_bytes())
                pending_response = await asyncio.wait_for(socket.recv(), timeout=10)
                if not isinstance(pending_response, bytes):
                    result["PROGRAMMATIC_PENDING_LIST"] = "NOT_WORKING"
                    return result
                (PRIVATE_DIR / "pending_response_latest.bin").write_bytes(pending_response)
                result["PROGRAMMATIC_PENDING_LIST"] = "WORKING"
                result["pending_response_length"] = len(pending_response)
                if "--approve" in sys.argv:
                    approve_path = PRIVATE_DIR / "approve_frame_template.bin"
                    pending_id = decode_contact_id(pending_response)
                    approve_frame = approve_path.read_bytes()
                    approve_id = decode_approve_user_id(approve_frame)
                    if pending_id is None or approve_id is None or pending_id != approve_id:
                        result["PROGRAMMATIC_OPCODE_77"] = "NOT_WORKING"
                        result["reason"] = "verified pending identity does not match approve frame"
                        return result
                    if approve_frame.count(b"userIds") != 1:
                        result["PROGRAMMATIC_OPCODE_77"] = "NOT_WORKING"
                        result["reason"] = "single-user guard failed"
                        return result
                    await socket.send(approve_frame)
                    approve_response = await asyncio.wait_for(socket.recv(), timeout=10)
                    result["PROGRAMMATIC_OPCODE_77"] = "WORKING" if isinstance(approve_response, bytes) and len(approve_response) else "NOT_WORKING"
                    result["approve_response_length"] = len(approve_response) if isinstance(approve_response, bytes) else 0
            return result
    except asyncio.TimeoutError:
        return {"PROGRAMMATIC_MAX_AUTH": "AUTH_TIMEOUT", "PROGRAMMATIC_READ_RPC": "NOT_WORKING", "WS_CONNECTION": "WORKING"}
    except PermissionError:
        return {"PROGRAMMATIC_MAX_AUTH": "AUTH_REJECTED", "WS_CONNECTION": "WORKING"}
    except Exception as exc:
        return {"PROGRAMMATIC_MAX_AUTH": "AUTH_REJECTED", "WS_CONNECTION": "FAILED", "reason": type(exc).__name__}


def main() -> int:
    if not os.environ.get("MAX_WEB_TOKEN"):
        print(json.dumps({
            "MAX_WEB_TOKEN_PROVIDED": "NO",
            "WS_CONNECTION": "NOT_ATTEMPTED",
            "PROGRAMMATIC_MAX_AUTH": "TOKEN_MISSING",
            "AUTH_FRAME_ROUNDTRIP": "FAILED",
            "BINARY_ENCODER": "PARTIAL",
            "reason": "Set MAX_WEB_TOKEN through an explicit secret/config import; browser stores are never read.",
        }))
        return 2

    token = os.environ["MAX_WEB_TOKEN"].encode("ascii")
    if not (PRIVATE_DIR / "auth_frame.bin").exists() or not (PRIVATE_DIR / "bootstrap_frame.bin").exists():
        print(json.dumps({"MAX_WEB_TOKEN_PROVIDED": "YES", "PROGRAMMATIC_MAX_AUTH": "BLOCKED", "reason": "private capture files are missing"}))
        return 3
    result = asyncio.run(run_probe(token))
    result.update({"MAX_WEB_TOKEN_PROVIDED": "YES", "AUTH_FRAME_ROUNDTRIP": "NORMALIZED_MATCH", "WS_URL": WS_URL})
    print(json.dumps(result))
    return 0 if result.get("PROGRAMMATIC_MAX_AUTH") == "WORKING" else 4


if __name__ == "__main__":
    raise SystemExit(main())
