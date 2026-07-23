import json
import zlib

import numpy as np
import pytest

from idraa.services.sample_codec import (
    SAMPLE_CODEC_MAGIC,
    decode_sample_arrays,
    decode_sample_arrays_np,
    encode_sample_arrays,
)


def test_round_trip_preserves_paths_and_values():
    arrays = {
        "base_risk": np.array([1.0, 2.5, 3.75e6], dtype=np.float64),
        "residual_risk": np.array([0.0, 10.0], dtype=np.float64),
        "per_scenario/0/base_risk": np.array([9.9], dtype=np.float64),
    }
    out = decode_sample_arrays(encode_sample_arrays(arrays))
    assert set(out) == set(arrays)
    for path, arr in arrays.items():
        np.testing.assert_allclose(out[path], arr, rtol=1e-6)
        assert isinstance(out[path], list)


def test_slash_paths_do_not_collide():
    arrays = {f"per_scenario/{i}/residual_risk": np.array([float(i)]) for i in range(5)}
    out = decode_sample_arrays(encode_sample_arrays(arrays))
    assert set(out) == set(arrays)


def test_empty_array_and_empty_dict_round_trip():
    assert decode_sample_arrays(encode_sample_arrays({"base_risk": np.empty(0)})) == {
        "base_risk": []
    }
    assert decode_sample_arrays(encode_sample_arrays({})) == {}


def test_compression_beats_json_ascii():
    rng = np.random.default_rng(1234)
    arr = rng.lognormal(mean=12.0, sigma=1.5, size=100_000)
    bytes_per_sample = len(encode_sample_arrays({"base_risk": arr})) / arr.size
    assert bytes_per_sample < 8.0  # target ~3–5; hard ceiling 8


def test_decode_rejects_non_codec_bytes():
    with pytest.raises(ValueError):
        decode_sample_arrays(b'{"base_risk": [1.0]}')


def test_decode_rejects_truncated_or_inconsistent_blob():
    # Sec-N1: guard (c) — header length must fit within the decompressed buffer.
    good = encode_sample_arrays({"base_risk": np.array([1.0, 2.0, 3.0])})
    # Plan-gate SWE2-B1 re-pin: this vector (16 bytes dropped) used to reach
    # back into the 4-byte header-length prefix and trip guard (c) directly.
    # The new eof/unconsumed_tail guard (Sec-N2) now fires FIRST on any
    # truncated deflate stream, so this vector hits the truncation guard
    # instead — a strictly better diagnostic for a chopped blob (it IS a
    # truncated stream, not a header-length lie), which is why the re-pin is
    # legitimate rather than a blind test weakening.
    with pytest.raises(ValueError, match=r"bound|truncated"):
        decode_sample_arrays(good[:-16])  # drop trailing float bytes → truncated stream


def test_decode_rejects_truncated_stream():
    # Plan-gate SWE2-B1 re-pin (renamed from test_decode_rejects_manifest_sum_mismatch):
    # the new eof/unconsumed_tail guard (Sec-N2) fires BEFORE the manifest-sum
    # check on any truncated blob, so this vector (originally targeting the
    # manifest-sum guard) now raises the truncation message instead — a
    # strictly better diagnostic for a chopped blob (it IS a truncated stream,
    # not a manifest lie), which is why the re-pin is legitimate.
    good = encode_sample_arrays({"base_risk": np.array([1.0, 2.0, 3.0])})
    with pytest.raises(ValueError, match=r"bound|truncated"):
        decode_sample_arrays(good[:-8])


def test_decode_rejects_manifest_length_lie():
    # Valid deflate stream, lying manifest: rebuild the container with a
    # wrong "len" so the eof guard passes and the sum check must catch it.
    blob = encode_sample_arrays({"base_risk": np.arange(8, dtype=np.float64)})
    raw = zlib.decompress(blob[len(SAMPLE_CODEC_MAGIC) :])
    hlen = int.from_bytes(raw[:4], "big")
    manifest = json.loads(raw[4 : 4 + hlen])
    manifest[0]["len"] = 7  # lie: payload holds 8 floats
    header = json.dumps(manifest, separators=(",", ":")).encode()
    forged = len(header).to_bytes(4, "big") + header + raw[4 + hlen :]
    forged_blob = SAMPLE_CODEC_MAGIC + zlib.compress(forged)
    with pytest.raises(ValueError, match="manifest lengths do not match"):
        decode_sample_arrays_np(forged_blob)


def test_decode_rejects_negative_manifest_length():
    blob = encode_sample_arrays(
        {"a": np.arange(4, dtype=np.float64), "b": np.arange(12, dtype=np.float64)}
    )
    raw = zlib.decompress(blob[len(SAMPLE_CODEC_MAGIC) :])
    hlen = int.from_bytes(raw[:4], "big")
    manifest = json.loads(raw[4 : 4 + hlen])
    manifest[0]["len"], manifest[1]["len"] = -4, 20  # sum still matches payload
    header = json.dumps(manifest, separators=(",", ":")).encode()
    forged = len(header).to_bytes(4, "big") + header + raw[4 + hlen :]
    forged_blob = SAMPLE_CODEC_MAGIC + zlib.compress(forged)
    with pytest.raises(ValueError, match="negative length"):
        decode_sample_arrays_np(forged_blob)


def test_decode_rejects_header_length_out_of_range():
    # Plan-gate SWE3-3: the hlen-range guard lost its only vector to the eof
    # guard above. Restore direct coverage with a forged valid-stream vector:
    # a valid deflate stream whose 4-byte prefix claims a header longer than
    # the remaining payload — must hit the hlen-range guard, not eof.
    blob = encode_sample_arrays({"base_risk": np.arange(4, dtype=np.float64)})
    raw = zlib.decompress(blob[len(SAMPLE_CODEC_MAGIC) :])
    forged = len(raw).to_bytes(4, "big") + raw[4:]
    forged_blob = SAMPLE_CODEC_MAGIC + zlib.compress(forged)
    with pytest.raises(ValueError, match="header length out of range"):
        decode_sample_arrays_np(forged_blob)


def test_decode_np_round_trips_float32_views() -> None:
    src = {
        "base_risk": np.array([1.5, 2.25, 3.125], dtype=np.float64),
        "per_scenario/0/base_risk": np.array([0.1, 0.2], dtype=np.float64),
    }
    blob = encode_sample_arrays(dict(src))
    out = decode_sample_arrays_np(blob)
    assert set(out) == set(src)
    for k, v in out.items():
        assert v.dtype == np.dtype("<f4")
        np.testing.assert_array_equal(v, src[k].astype(np.float32))


def test_decode_np_rejects_bad_magic() -> None:
    with pytest.raises(ValueError):
        decode_sample_arrays_np(b"NOTRFSC" + b"\x00" * 16)


def test_decode_np_matches_list_decoder() -> None:
    src = {"residual_risk": np.linspace(0.0, 9.75, 40)}
    blob = encode_sample_arrays(dict(src))
    as_np = decode_sample_arrays_np(blob)["residual_risk"]
    as_list = decode_sample_arrays(blob)["residual_risk"]
    assert [float(x) for x in as_np.astype(np.float64)] == as_list


def test_decode_rejects_stream_exceeding_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sec-N2: decompress(max_length) truncates silently; the decoder must
    # reject rather than parse a truncated buffer. Shrink the bound to test.
    from idraa.services import sample_codec

    blob = encode_sample_arrays({"base_risk": np.arange(1000, dtype=np.float64)})
    monkeypatch.setattr(sample_codec, "_MAX_DECOMPRESSED_BYTES", 64)
    with pytest.raises(ValueError, match=r"bound|truncated"):
        decode_sample_arrays_np(blob)
