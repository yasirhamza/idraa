"""The key wrapper: clean environment, own commands only, no dirty tree (spec §6)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[3] / "scripts" / "curation-check"


def _run(
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    script = WRAPPER if cwd is None else cwd / "scripts" / "curation-check"
    return subprocess.run([str(script), *args], capture_output=True, text=True, cwd=cwd, env=env)


@pytest.mark.parametrize(
    "args",
    [
        ("bogus",),
        ("labels", "--judge", "replay"),
        ("labels", "--key-stdin"),
        ("labels", "--top", "abc"),
        ("labels", "--campaign", "Bad Name"),
        ("gaps", "--intake", "/definitely/not/here.jsonl"),
        (),
    ],
)
def test_wrapper_refuses_unexpected_arguments(args: tuple[str, ...]) -> None:
    result = _run(*args)
    assert result.returncode == 2
    assert "TYPESAFE_API_KEY=" not in result.stdout + result.stderr


def test_wrapper_refuses_other_keychain_services() -> None:  # S-B2
    env = {**os.environ, "CURATION_KEYCHAIN_SERVICE": "idraa-cloudflare-token"}
    result = _run("labels", env=env)
    assert result.returncode == 2
    assert "CURATION_KEYCHAIN_SERVICE" in result.stderr


def test_wrapper_ignores_bash_env(tmp_path: Path) -> None:  # S-B2: shebang runs bash -p
    marker = tmp_path / "ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"touch {marker}\n")
    result = _run("bogus", env={**os.environ, "BASH_ENV": str(hook)})
    assert result.returncode == 2
    assert not marker.exists()


def test_wrapper_refuses_a_forged_clean_stage() -> None:  # I-1r2
    env = {**os.environ, "_CC_CLEAN": "1", "_CC_SERVICE": "idraa-cloudflare-token"}
    result = _run("labels", env=env)
    assert result.returncode == 2
    env = {
        **os.environ,
        "_CC_CLEAN": "1",
        "_CC_SERVICE": "idraa-typesafe-key",
    }  # valid name, dirty environment
    assert _run("labels", env=env).returncode == 2


def test_wrapper_refuses_to_run_via_bash() -> (
    None
):  # N-2r2: `bash script` would skip the -p shebang
    result = subprocess.run(["bash", str(WRAPPER), "labels"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "run it directly" in result.stderr


def test_wrapper_refuses_uncommitted_changes(tmp_path: Path) -> None:
    (tmp_path / "scripts" / "curation_check").mkdir(parents=True)
    (tmp_path / "data" / "curation").mkdir(parents=True)
    shutil.copy2(WRAPPER, tmp_path / "scripts" / "curation-check")
    (tmp_path / "scripts" / "curation_check" / "__init__.py").write_text("")
    (tmp_path / "data" / "curation" / "criteria.json").write_text("{}")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([*git, "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / "data" / "curation" / "criteria.json").write_text('{"changed": true}')
    result = _run("labels", cwd=tmp_path)
    assert result.returncode == 1
    assert "uncommitted changes" in result.stderr
