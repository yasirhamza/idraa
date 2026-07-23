import numpy as np
import pytest

from idraa.services.sample_codec import (
    decode_sample_arrays,
    encode_sample_arrays,
    encode_sample_arrays_streaming,
)


def test_streaming_decodes_identically_to_batch():
    arrays = {
        "base_risk": np.array([1.0, 2.0, 3.0]),
        "per_scenario/0/residual_risk": np.array([4.0, 5.0]),
    }
    batch = decode_sample_arrays(encode_sample_arrays(dict(arrays)))
    stream = decode_sample_arrays(encode_sample_arrays_streaming(dict(arrays)))
    assert stream == batch


def test_streaming_empties_the_input_dict():
    arrays = {"base_risk": np.array([1.0, 2.0])}
    encode_sample_arrays_streaming(arrays)
    assert arrays == {}  # popped as encoded, so the caller's refs are released


def test_streaming_rejects_float32_overflow():
    # Sec-L8/#84: same fail-closed guard as the batch encoder.
    arrays = {"base_risk": np.array([1.0, 1e50, 3.0], dtype=np.float64)}
    with pytest.raises(ValueError, match="codec overflow"):
        encode_sample_arrays_streaming(arrays)
