"""The key wrapper: clean environment, own commands only, no dirty tree (spec §6)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[3] / "scripts" / "curation-check"
LAUNCH = Path(__file__).resolve().parents[3] / "scripts" / "curation_check" / "_launch.py"
# Tests that could get past a refusal if the wrapper were broken name a Keychain item that does not exist,
# so a regression fails on "no Keychain item" instead of reading the real key.
ABSENT_SERVICE = "idraa-typesafe-key-test-absent"


def _safe_env(
    env: dict[str, str] | None = None, *, service: str = ABSENT_SERVICE
) -> dict[str, str]:
    """Always point the wrapper at a Keychain item that does not exist (overriding anything the developer's
    shell exports) and drop any real key, so no regression can ever read or send a real secret."""
    out = dict(os.environ if env is None else env)
    out.pop("TYPESAFE_API_KEY", None)
    out["CURATION_KEYCHAIN_SERVICE"] = (
        service  # also on forged-stage runs: an exported real name never survives
    )
    return out


def _run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    service: str = ABSENT_SERVICE,
) -> subprocess.CompletedProcess[str]:
    script = WRAPPER if cwd is None else cwd / "scripts" / "curation-check"
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=_safe_env(env, service=service),
    )


@pytest.mark.parametrize(
    "args",
    [
        ("bogus",),
        ("labels", "--judge", "replay"),
        ("labels", "--key-stdin"),
        ("labels", "--top", "abc"),
        ("labels", "--campaign", "Bad Name"),
        ("gaps", "--intake", "/definitely/not/here.jsonl"),
        ("tally", "--top", "5"),
        (),
    ],
)
def test_wrapper_refuses_unexpected_arguments(args: tuple[str, ...]) -> None:
    result = _run(*args)
    assert result.returncode == 2
    assert "TYPESAFE_API_KEY=" not in result.stdout + result.stderr


def test_wrapper_refuses_other_keychain_services() -> None:  # S-B2
    # another project's naming pattern, but an item that does not exist: a regression can never read a real secret
    result = _run("labels", service="idraa-cloudflare-token-test-absent")
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
    env = {**os.environ, "_CC_CLEAN": "1", "_CC_SERVICE": "idraa-cloudflare-token-test-absent"}
    result = _run("labels", env=env)
    assert result.returncode == 2
    # valid name, dirty environment; the -test-absent item does not exist, so a broken check can never read the real key
    env = {**os.environ, "_CC_CLEAN": "1", "_CC_SERVICE": ABSENT_SERVICE}
    assert _run("labels", env=env).returncode == 2


def test_wrapper_refuses_to_run_via_bash() -> (
    None
):  # N-2r2: `bash script` would skip the -p shebang
    result = subprocess.run(
        ["bash", str(WRAPPER), "labels"], capture_output=True, text=True, env=_safe_env()
    )
    assert result.returncode == 2
    assert "run it directly" in result.stderr


GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


def _scratch_repo(root: Path) -> Path:
    (root / "scripts" / "curation_check").mkdir(parents=True)
    (root / "data" / "curation").mkdir(parents=True)
    shutil.copy2(WRAPPER, root / "scripts" / "curation-check")
    (root / "scripts" / "curation_check" / "__init__.py").write_text("")
    (root / "data" / "curation" / "criteria.json").write_text("{}")
    subprocess.run([*GIT, "init", "-q"], cwd=root, check=True)
    subprocess.run([*GIT, "add", "-A"], cwd=root, check=True)
    subprocess.run([*GIT, "commit", "-qm", "init"], cwd=root, check=True)
    return root


def _run_scratch(root: Path) -> subprocess.CompletedProcess[str]:
    return _run("labels", cwd=root)


def test_wrapper_refuses_uncommitted_changes(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    (repo / "data" / "curation" / "criteria.json").write_text('{"changed": true}')
    result = _run_scratch(repo)
    assert result.returncode == 1
    assert "uncommitted changes" in result.stderr


def test_wrapper_sees_untracked_files_git_config_would_hide(
    tmp_path: Path,
) -> None:  # Task-9 security review
    repo = _scratch_repo(tmp_path)
    subprocess.run([*GIT, "config", "status.showUntrackedFiles", "no"], cwd=repo, check=True)
    (repo / "scripts" / "curation_check" / "extra.py").write_text("")
    result = _run_scratch(repo)
    assert result.returncode == 1
    assert "uncommitted changes" in result.stderr


def test_wrapper_refuses_skip_worktree_hidden_edits(
    tmp_path: Path,
) -> None:  # Task-9 security review
    repo = _scratch_repo(tmp_path)
    subprocess.run(
        [*GIT, "update-index", "--skip-worktree", "data/curation/criteria.json"],
        cwd=repo,
        check=True,
    )
    (repo / "data" / "curation" / "criteria.json").write_text('{"changed": true}')
    result = _run_scratch(repo)
    assert result.returncode == 1
    assert "hidden changes" in result.stderr


def test_launcher_ignores_modules_planted_at_the_root(
    tmp_path: Path,
) -> None:  # Task-9 security review
    pkg = tmp_path / "scripts" / "curation_check"
    pkg.mkdir(parents=True)
    (tmp_path / "scripts" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    ran, planted = tmp_path / "ran", tmp_path / "planted"
    (pkg / "__main__.py").write_text(
        f"import json\nopen({str(ran)!r}, 'w').write(json.dumps('ok'))\n"
    )
    (tmp_path / "json.py").write_text(f"open({str(planted)!r}, 'w').write('x')\n")
    # -S keeps the venv's editable fair_cam .pth (which adds the real checkout root) out of this toy run;
    # the launcher's own guarantee (stdlib before ROOT, no working directory on sys.path) is unchanged.
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(LAUNCH), str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert ran.read_text() == '"ok"'
    assert not planted.exists()
