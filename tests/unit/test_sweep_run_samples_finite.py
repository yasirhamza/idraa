"""Tests for scripts/sweep_run_samples_finite.py (idraa#131 part 2).

The corrupt fixture is hand-crafted in the PRE-#99 container shape (no
finite guard) — exactly what a legacy corrupt row would look like, since the
current encoder fail-closes on non-finite float32 and cannot produce one.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import zlib
from pathlib import Path

import numpy as np
import pytest

from idraa.services.sample_codec import SAMPLE_CODEC_MAGIC

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sweep_run_samples_finite.py"
_spec = importlib.util.spec_from_file_location("sweep_run_samples_finite", _SCRIPT)
assert _spec is not None and _spec.loader is not None
sweep_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep_mod)


def _craft_legacy_blob(arrays: dict[str, np.ndarray]) -> bytes:
    """Pre-#99 encoder shape WITHOUT the finite guard."""
    manifest: list[dict[str, object]] = []
    chunks: list[bytes] = []
    for path, arr in arrays.items():
        a = np.ascontiguousarray(arr, dtype="<f4")
        manifest.append({"path": path, "len": int(a.size)})
        chunks.append(a.tobytes())
    header = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    raw = len(header).to_bytes(4, "big") + header + b"".join(chunks)
    return SAMPLE_CODEC_MAGIC + zlib.compress(raw)


def _mk_db(tmp_path: Path, rows: list[tuple[str, str | None, bytes | None]]) -> Path:
    db = tmp_path / "sweep-fixture.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE run_samples (run_id TEXT PRIMARY KEY, arrays TEXT, arrays_codec BLOB)"
    )
    conn.executemany("INSERT INTO run_samples VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return db


def test_clean_rows_verdict_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blob = _craft_legacy_blob({"base/risk": np.array([1.0, 2.5, 3e30])})
    db = _mk_db(tmp_path, [("run-1", None, blob), ("run-2", '{"a": [1.0]}', None)])
    rc = sweep_mod.sweep(db)
    out = capsys.readouterr().out
    assert rc == 0
    assert "codec rows swept:      1" in out
    assert "legacy JSON-only rows: 1" in out
    assert "clean" in out


def test_non_finite_row_detected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = _craft_legacy_blob(
        {"base/risk": np.array([1.0, np.inf, 2.0]), "residual/risk": np.array([np.nan])}
    )
    db = _mk_db(tmp_path, [("run-bad", None, bad)])
    rc = sweep_mod.sweep(db)
    out = capsys.readouterr().out
    assert rc == 1
    assert "NON-FINITE" in out
    assert "base/risk: 1/3 non-finite" in out
    assert "residual/risk: 1/1 non-finite" in out


def test_undecodable_blob_is_a_finding(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = _mk_db(tmp_path, [("run-garbage", None, b"not a codec blob at all")])
    rc = sweep_mod.sweep(db)
    out = capsys.readouterr().out
    assert rc == 1
    assert "DECODE-FAIL" in out


def test_sweep_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blob = _craft_legacy_blob({"x": np.array([np.inf])})
    db = _mk_db(tmp_path, [("run-1", None, blob)])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    sweep_mod.sweep(db)
    capsys.readouterr()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
