"""Deterministic autonomous creative candidate and image asset builder.

V1.1 deliberately keeps the generator small and auditable: it creates a
content-addressed PNG placeholder carrying the experiment identity.  The
provider adapter is responsible for uploading the asset and returning the
provider image hash; PostgreSQL stores metadata only.
"""
from __future__ import annotations

import binascii
import hashlib
import struct
import zlib
from typing import Any


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    row = bytes([0]) + bytes(rgb) * width
    raw = row * height
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def build_creative_candidate(*, campaign_id: str, experiment_id: str, objective: str | None,
                             target_cpa: float | None, control: dict[str, Any] | None = None,
                             hypothesis: str | None = None) -> dict[str, Any]:
    """Return a complete provider-neutral candidate without owner payload input."""
    seed = hashlib.sha256(f"{campaign_id}:{experiment_id}".encode()).hexdigest()
    headline = (hypothesis or "Новый вариант объявления")[:56]
    text = f"Тестовый вариант для {objective or 'целевого результата'}"
    if target_cpa is not None:
        text += f" до {float(target_cpa):g} RUB"
    image = _png(1200, 628, (int(seed[0:2], 16), int(seed[2:4], 16), int(seed[4:6], 16)))
    return {
        "headline": headline,
        "text": text[:255],
        "cta": "Узнать больше",
        "creative_concept": hypothesis or "bounded_autonomous_variant",
        "control_reference": control or {},
        "image_bytes": image,
        "mime_type": "image/png",
        "extension": "png",
        "content_hash": hashlib.sha256(image).hexdigest(),
        "campaign_id": campaign_id,
        "experiment_id": experiment_id,
        "treatment_specification": {"variable": "creative", "objective": objective, "target_cpa": target_cpa},
    }
