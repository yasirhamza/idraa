"""papermill smoke tests — re-run the fair_cam validation notebooks.

These catch library-level regressions: if fair_cam ever changes in a way that
breaks the canonical example, this test fails.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

NOTEBOOKS_DIR = Path(__file__).resolve().parents[2] / "notebooks"


@pytest.mark.notebook
@pytest.mark.slow
def test_fair_cam_example_notebook_runs_clean() -> None:
    """notebooks/fair_cam_example.ipynb must execute cell-by-cell without error."""
    import papermill as pm

    input_path = NOTEBOOKS_DIR / "fair_cam_example.ipynb"
    assert input_path.exists(), f"expected {input_path} to exist"

    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "output.ipynb"
        pm.execute_notebook(
            str(input_path),
            str(output_path),
            kernel_name="python3",
            progress_bar=False,
        )
        # Papermill raises PapermillExecutionError on failure — no further assert needed.
        assert output_path.stat().st_size > 0
