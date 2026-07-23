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


def decode_sample_arrays(blob: bytes) -> dict[str, list[float]]:
    if not blob.startswith(SAMPLE_CODEC_MAGIC):
        raise ValueError("not an Idraa sample-codec blob")
    # Sec-N1: bound the decompression (defends a corrupt/crafted row).
    raw = zlib.decompressobj().decompress(blob[len(SAMPLE_CODEC_MAGIC) :], _MAX_DECOMPRESSED_BYTES)
    if len(raw) < 4:
        raise ValueError("codec blob truncated (no header length)")
    hlen = int.from_bytes(raw[:4], "big")
    if not (0 <= hlen <= len(raw) - 4):
        raise ValueError("codec header length out of range")
    manifest = json.loads(raw[4 : 4 + hlen].decode("utf-8"))
    buf = raw[4 + hlen :]
    if sum(int(e["len"]) for e in manifest) * 4 != len(buf):
        raise ValueError("codec manifest lengths do not match payload buffer")
    out: dict[str, list[float]] = {}
    off = 0
    for entry in manifest:
        nbytes = int(entry["len"]) * 4
        arr = np.frombuffer(buf[off : off + nbytes], dtype="<f4")
        out[str(entry["path"])] = arr.astype(np.float64).tolist()
        off += nbytes
    return out


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
