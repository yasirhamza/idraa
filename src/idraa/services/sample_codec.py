"""Compressed binary codec for the heavy per-iteration Monte Carlo arrays.

Replaces the JSON-ASCII persistence of ``run_samples.arrays`` (~20 B/float, plus
an O(M·N) Python-list materialization at write time). Arrays are packed as
float32 little-endian bytes behind a JSON manifest, then DEFLATE-compressed
(stdlib ``zlib`` — no third-party dependency). float32 is the STORED precision
only; every reported metric is computed upstream from the original float64 array.

Container layout (post-magic, post-DEFLATE-decompress):
    [4 bytes big-endian: header length H]
    [H bytes: UTF-8 JSON manifest = list of {"path": str, "len": int}]
    [concatenated float32 LE bytes, in manifest order]

Two decode variants share one validated parse: ``decode_sample_arrays`` returns
the legacy ``dict[str, list[float]]`` contract (float64 lists); ``decode_sample_arrays_np``
returns zero-copy float32 ``np.ndarray`` views for memory-bounded consumers (CSV export).
"""

from __future__ import annotations

import json
import zlib

import numpy as np

SAMPLE_CODEC_MAGIC = b"RFSC1"
_DEFLATE_LEVEL = 6
# Sec-N1: bound the decompression (defends a corrupt/crafted row). 2 GiB, not 8 —
# the deployment VM's memory envelope means an 8 GiB bound could OOM the process before the
# guard even engages. 2 GiB sits comfortably above the ~248 MB real max observed at
# M=30 scenarios / 1M iterations while still meaningfully protecting the VM.
_MAX_DECOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


def encode_sample_arrays(arrays: dict[str, np.ndarray]) -> bytes:
    manifest: list[dict[str, object]] = []
    chunks: list[bytes] = []
    for path, arr in arrays.items():
        a = np.ascontiguousarray(arr, dtype="<f4")
        manifest.append({"path": path, "len": int(a.size)})
        chunks.append(a.tobytes())
    header = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    raw = len(header).to_bytes(4, "big") + header + b"".join(chunks)
    return SAMPLE_CODEC_MAGIC + zlib.compress(raw, _DEFLATE_LEVEL)


def _parse_container(blob: bytes) -> dict[str, np.ndarray]:
    """Validate + parse the codec container to float32 LE views.

    Zero-copy discipline (plan-gate Sec-I3/SWE-I2): every view is built with
    offset-based np.frombuffer over the ONE decompressed buffer — bytes
    slicing would COPY (peak ~3x decompressed size, ~750 MB at the observed
    248 MB max, on the VM PR #216 already OOM-bumped once). Views are
    read-only (frombuffer over immutable bytes).
    """
    if not blob.startswith(SAMPLE_CODEC_MAGIC):
        raise ValueError("not an Idraa sample-codec blob")
    # Sec-N1: bound the decompression (defends a corrupt/crafted row).
    d = zlib.decompressobj()
    raw = d.decompress(blob[len(SAMPLE_CODEC_MAGIC) :], _MAX_DECOMPRESSED_BYTES)
    # Plan-gate Sec-N2: decompress(max_length) TRUNCATES silently rather than
    # raising — reject anything that didn't fit or didn't finish cleanly.
    if not d.eof or d.unconsumed_tail:
        raise ValueError("codec blob exceeds decompression bound or is truncated")
    if len(raw) < 4:
        raise ValueError("codec blob truncated (no header length)")
    hlen = int.from_bytes(raw[:4], "big")
    if not (0 <= hlen <= len(raw) - 4):
        raise ValueError("codec header length out of range")
    manifest = json.loads(raw[4 : 4 + hlen].decode("utf-8"))
    payload_len = len(raw) - 4 - hlen
    if sum(int(e["len"]) for e in manifest) * 4 != payload_len:
        raise ValueError("codec manifest lengths do not match payload buffer")
    out: dict[str, np.ndarray] = {}
    off = 4 + hlen
    for entry in manifest:
        count = int(entry["len"])
        if count < 0:  # negative lens can satisfy the sum check yet walk offsets backwards
            raise ValueError("codec manifest entry has negative length")
        out[str(entry["path"])] = np.frombuffer(raw, dtype="<f4", count=count, offset=off)
        off += count * 4
    return out


def decode_sample_arrays(blob: bytes) -> dict[str, list[float]]:
    return {p: a.astype(np.float64).tolist() for p, a in _parse_container(blob).items()}


def decode_sample_arrays_np(blob: bytes) -> dict[str, np.ndarray]:
    """float32 views for memory-bounded consumers (CSV export). Read-only
    (frombuffer over immutable bytes); list contract of decode_sample_arrays
    is untouched."""
    return _parse_container(blob)


def encode_sample_arrays_streaming(arrays: dict[str, np.ndarray]) -> bytes:
    """Peak-bounded encoder: pops each array as it is compressed so the float64
    originals and float32 copies never all co-exist. Decodes identically to
    encode_sample_arrays; MUTATES (empties) the input dict.
    """
    manifest = [{"path": p, "len": int(np.asarray(a).size)} for p, a in arrays.items()]
    header = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    co = zlib.compressobj(_DEFLATE_LEVEL)
    out: list[bytes] = [SAMPLE_CODEC_MAGIC, co.compress(len(header).to_bytes(4, "big") + header)]
    for path in [str(e["path"]) for e in manifest]:
        a = np.ascontiguousarray(arrays.pop(path), dtype="<f4")
        out.append(co.compress(a.tobytes()))
        del a
    out.append(co.flush())
    return b"".join(out)
