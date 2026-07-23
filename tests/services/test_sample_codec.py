import numpy as np
import pytest

from idraa.services.sample_codec import (
    decode_sample_arrays,
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
    # zlib's stream trailer (final block + Adler-32 checksum) absorbs a few
    # trailing bytes without affecting the decompressed payload, so a small
    # truncation must be large enough to actually reach into the float
    # payload bytes (verified empirically: <6 bytes dropped is a no-op here).
    # This large a truncation (16 bytes) reaches back into the 4-byte header
    # length prefix itself, tripping guard (c) — "codec header length out of
    # range" — not the manifest-sum guard (d).
    with pytest.raises(ValueError):
        decode_sample_arrays(good[:-16])  # drop trailing float bytes → length mismatch


def test_encode_rejects_float32_overflow():
    # Sec-L8/#84: a float64 value that overflows on cast to float32 (source
    # magnitude exceeds ~3.4e38) must fail closed at write time rather than
    # silently persist inf into the codec blob.
    arrays = {"base_risk": np.array([1.0, 1e50, 3.0], dtype=np.float64)}
    with pytest.raises(ValueError, match="codec overflow"):
        encode_sample_arrays(arrays)


def test_decode_rejects_manifest_sum_mismatch():
    # Sec-N1: guard (d) — manifest-declared lengths must sum to match the
    # payload buffer. Dropping 8 bytes here removes float payload bytes without
    # reaching back into the 4-byte header-length prefix, so it trips guard (d)
    # specifically (verified empirically over the good[:-6]..good[:-15] range).
    good = encode_sample_arrays({"base_risk": np.array([1.0, 2.0, 3.0])})
    with pytest.raises(ValueError, match="manifest lengths do not match payload buffer"):
        decode_sample_arrays(good[:-8])
