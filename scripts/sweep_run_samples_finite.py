"""One-off READ-ONLY overflow sweep of run_samples.arrays_codec (idraa#131).

The float32 codec BLOB was the one durable write channel with no non-finite
backstop until PR #99 (strict_json_dumps already fail-closes the JSON
channel). Per the "code fix is half the fix" convention (#346 precedent),
this sweeps every legacy codec row and reports any array containing a
non-finite value (inf / -inf / nan) — the corruption class #99's encoder now
rejects at write time.

Usage:
    python scripts/sweep_run_samples_finite.py /path/to/idraa.db

Strictly read-only: the DB is opened with SQLite URI mode=ro. Caveats:
- WAL: mode=ro fails ("attempt to write a readonly database") against a raw
  `cp` of a live WAL database whose -wal sidecar needs recovery. Run against
  the live DB (the app holds it open) or a clean ONLINE-backup copy (the
  ~/idraa-backups recipe produces those).
- Memory: rows are decoded one at a time, but a single 1M-iteration
  AGGREGATE row can decompress to the 2 GiB codec bound — run this OFF-BOX
  (against a backup) rather than on the 2 GB prod VM.

Legacy JSON-channel rows (arrays populated, arrays_codec NULL) are counted
but not decoded — that channel was always guarded by strict_json_dumps.
Rows with BOTH channels NULL violate the run_samples writer invariant and
are reported as findings.

Exit codes: 0 = clean; 1 = finding(s) present — non-finite values, decode
failures, or writer-invariant violations (repair per the #346
gated-migration pattern is a SEPARATE, deliberate step); 2 = usage.
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import numpy as np

from idraa.services.sample_codec import decode_sample_arrays_np


def _canonical(run_id: object) -> str:
    """Render the CHAR(32) no-hyphen SQLite storage form as a canonical UUID
    (the repo's recurring seed-UUID foot-gun) so ids paste into URLs/queries."""
    try:
        return str(uuid.UUID(str(run_id)))
    except ValueError:
        return str(run_id)


def sweep(db_path: Path) -> int:
    # as_uri() percent-escapes the path (spaces, '?', '#') and yields a
    # cross-platform file: URI (review: bare f-string broke on those).
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT run_id, arrays_codec IS NOT NULL, arrays IS NOT NULL FROM run_samples"
        ).fetchall()
        total = len(rows)
        codec_rows = [(r[0],) for r in rows if r[1]]
        json_only = sum(1 for r in rows if not r[1] and r[2])
        both_null = [str(r[0]) for r in rows if not r[1] and not r[2]]

        corrupt: list[tuple[str, str]] = []  # (run_id, description)
        decode_failures: list[tuple[str, str]] = []
        for (run_id,) in codec_rows:
            blob = conn.execute(
                "SELECT arrays_codec FROM run_samples WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            try:
                arrays = decode_sample_arrays_np(blob)
            except Exception as exc:  # decode failure is itself a finding
                decode_failures.append((_canonical(run_id), f"{type(exc).__name__}: {exc}"))
                continue
            for key, arr in arrays.items():
                bad = int(np.count_nonzero(~np.isfinite(arr)))
                if bad:
                    corrupt.append((_canonical(run_id), f"{key}: {bad}/{arr.size} non-finite"))

        print(f"run_samples rows:        {total}")
        print(f"  codec rows swept:      {len(codec_rows)}")
        print(
            f"  legacy JSON-only rows: {json_only} (channel guarded by strict_json_dumps; skipped)"
        )
        print(f"  both-channels-NULL:    {len(both_null)} (writer-invariant violations)")
        print(f"  decode failures:       {len(decode_failures)}")
        print(f"  non-finite findings:   {len(corrupt)}")
        for rid in both_null:
            print(f"  BOTH-NULL   run {_canonical(rid)}: arrays AND arrays_codec NULL")
        for run_id, desc in decode_failures:
            print(f"  DECODE-FAIL run {run_id}: {desc}")
        for run_id, desc in corrupt:
            print(f"  NON-FINITE  run {run_id}: {desc}")
        if corrupt or decode_failures or both_null:
            print(
                "VERDICT: findings present — repair/annotate per the #346 gated-migration pattern."
            )
            return 1
        print("VERDICT: clean — no legacy codec row carries a non-finite value.")
        return 0
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    db_path = Path(argv[1])
    if not db_path.exists():
        print(f"no such file: {db_path}")
        return 2
    return sweep(db_path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
