"""Guard: the runtime image pins PYTHONHASHSEED (issue #33).

The weight_robustness ensemble blob is a reproducibility-pinned artifact,
but set/frozenset iteration order in fair_cam's scalar compose path is
hash-seed dependent and float accumulation is non-associative — so an
unpinned interpreter seed breaks byte-reproducibility at the ~1e-13 level
across process launches. The pin must live at interpreter start (Dockerfile
ENV + fly.toml [env]); setting os.environ at runtime is too late. If this
test fires, restore the pin rather than deleting the test — see issue #33
option 1 (option 2, deterministic operand ordering in the compose
primitives, is the invasive alternative and needs a golden re-baseline).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_runtime_pins_hashseed() -> None:
    text = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = text[text.index(" AS runtime") :]
    assert "PYTHONHASHSEED=0" in runtime


def test_fly_toml_pins_hashseed() -> None:
    assert 'PYTHONHASHSEED = "0"' in (_ROOT / "fly.toml").read_text(encoding="utf-8")
