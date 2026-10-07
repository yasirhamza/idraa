"""The clerk stage of scripts/run_local_gate.py (adoption design §4). Nothing here runs uv, git
or the clerk: `_git`, `_run` and `subprocess.run` are monkeypatched where needed."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from scripts import run_local_gate as g

SHA = "b9499b7975084e550cc40a02f8e8cfaf840934a9"
ROOT = "/plugins/superpowers-clerk"
PREFIX = ["uv", "run", "--frozen", "--project", ROOT, "clerk", "gate"]
# What `git ls-tree -z <rev> -- clerk.toml` prints when the path exists at <rev>.
PRESENT = "100644 blob 0123456789abcdef0123456789abcdef01234567\tclerk.toml\0"


def test_clerk_skipped_when_root_unset() -> None:
    reason = g.clerk_skip_reason({})
    assert reason is not None and g.CLERK_ROOT_ENV in reason and "CI always skips" in reason
    # An exported-but-empty variable is "unset", not a root named "".
    reason = g.clerk_skip_reason({g.CLERK_ROOT_ENV: ""})
    assert reason is not None and g.CLERK_ROOT_ENV in reason and "CI always skips" in reason


def test_clerk_skipped_by_escape_hatch() -> None:
    reason = g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x", g.SKIP_CLERK_ENV: "1"})
    assert reason is not None and g.SKIP_CLERK_ENV in reason


def test_clerk_runs_when_root_set() -> None:
    assert g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x"}) is None
    assert g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x", g.SKIP_CLERK_ENV: "0"}) is None


def test_clerk_env_names_and_origin_ref_are_the_documented_literals() -> None:
    # Users export these names by hand; the origin ref must not be an ambiguous short name
    # (a local tag or branch called origin/main shadows it).
    assert (g.CLERK_ROOT_ENV, g.SKIP_CLERK_ENV) == ("IDRAA_CLERK_ROOT", "IDRAA_GATE_SKIP_CLERK")
    assert g.CLERK_ORIGIN_REF == "refs/remotes/origin/main"


def test_clerk_mode_decision() -> None:
    assert g.clerk_mode(at_base=True, at_origin_main=True) == "normal"
    assert g.clerk_mode(at_base=True, at_origin_main=False) == "normal"
    assert g.clerk_mode(at_base=False, at_origin_main=True) == "refuse"
    assert g.clerk_mode(at_base=False, at_origin_main=False) == "bootstrap"


def test_clerk_command_shape_normal() -> None:
    assert g.clerk_gate_command(ROOT, SHA, mode="normal") == [
        *PREFIX,
        "citations",
        "hygiene",
        "--config",
        g.CLERK_CONFIG,
        "--base",
        SHA,
        "--manifest",
        g.CLERK_MANIFEST,
        "--range",
        f"{SHA}..HEAD",
    ]
    assert g.CLERK_CONFIG == "clerk.toml" and g.CLERK_MANIFEST == ".clerk/manifests/citations.json"


def test_clerk_command_shape_bootstrap() -> None:
    assert g.clerk_gate_command(ROOT, SHA, mode="bootstrap") == [
        *PREFIX,
        "hygiene",
        "--config",
        g.CLERK_CONFIG,
        "--base",
        SHA,
        "--range",
        f"{SHA}..HEAD",
        "--trust-worktree-config",
    ]


def test_clerk_command_shape_surfaces() -> None:
    assert g.clerk_gate_command(ROOT, SHA, mode="surfaces") == [
        *PREFIX,
        "surfaces",
        "--config",
        g.CLERK_CONFIG,
        "--base",
        SHA,
    ]
    with pytest.raises(ValueError):
        g.clerk_gate_command(ROOT, SHA, mode="refuse")


def test_clerk_step_is_outside_gate_steps() -> None:
    labels = [label for label, _ in g.GATE_STEPS]
    assert "clerk gate" not in labels and len(labels) == 7
    assert g.steps_to_run(env={g.CLERK_ROOT_ENV: "/x"}) == list(g.GATE_STEPS)


def test_clean_env_strips_git_and_virtualenv(monkeypatch: pytest.MonkeyPatch) -> None:
    leaked = {
        "GIT_DIR": "/i/.git/worktrees/x",
        "GIT_INDEX_FILE": "/i/.git/worktrees/x/index",
        "GIT_CONFIG_PARAMETERS": "'core.bare'='true'",
        "VIRTUAL_ENV": "/i/.venv",
        "IDRAA_KEEP": "1",
    }
    for key, value in leaked.items():
        monkeypatch.setenv(key, value)
    env = g._clean_env()
    assert [k for k in env if k.startswith("GIT_")] == []
    assert "VIRTUAL_ENV" not in env
    assert env["IDRAA_KEEP"] == "1"


def test_git_and_run_pass_the_clean_env_their_cwd_and_never_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 3, "", "")

    monkeypatch.setenv("GIT_DIR", "/leak")
    monkeypatch.setenv("VIRTUAL_ENV", "/venv")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert g._git("describe", cwd="/clerk").returncode == 3
    assert g._git("merge-base").returncode == 3
    assert g._run(["uv", "run"]) == 3  # returned, never raised
    (describe, dkw), (merge_base, mkw), (uv, rkw) = seen
    assert describe == ["git", "describe"] and merge_base == ["git", "merge-base"]
    assert uv == ["uv", "run"]
    assert dkw["cwd"] == "/clerk"
    assert mkw["cwd"] == rkw["cwd"] == g.REPO_ROOT
    assert dkw["capture_output"] is True and dkw["text"] is True
    for kwargs in (dkw, mkw, rkw):
        assert kwargs["check"] is False
        assert "GIT_DIR" not in kwargs["env"] and "VIRTUAL_ENV" not in kwargs["env"]


class _FakeGit:
    """`_git` stand-in answering what real git answers, and recording what it was asked.

    ls-tree `at_*`: True = path present (rc 0, one NUL-terminated entry), False = absent
    (rc 0, empty), None = git failed (rc 128). `calls` is `(args, cwd)` per call so the tests
    can pin WHICH questions are asked, of WHICH repository.
    """

    def __init__(
        self,
        merge_base: int = 0,
        at_base: bool | None = True,
        at_origin: bool | None = True,
        sha: str = SHA,
        merge_base_stderr: str = "",
    ) -> None:
        self.merge_base = merge_base
        self.at = {"base": at_base, "origin": at_origin}
        self.sha = sha
        self.merge_base_stderr = merge_base_stderr
        self.calls: list[tuple[tuple[str, ...], object]] = []

    def __call__(
        self, *args: str, cwd: Path | str | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, cwd))
        if args[0] == "describe":
            return subprocess.CompletedProcess(args, 0, "v0.1.0\n", "")
        if args[0] == "merge-base":
            if self.merge_base:
                return subprocess.CompletedProcess(
                    args, self.merge_base, "", self.merge_base_stderr
                )
            return subprocess.CompletedProcess(args, 0, self.sha + "\n", "")
        if args[0] == "ls-tree":
            state = self.at["origin" if args[2] == g.CLERK_ORIGIN_REF else "base"]
            if state is None:
                return subprocess.CompletedProcess(args, 128, "", "fatal: not a tree object")
            return subprocess.CompletedProcess(args, 0, PRESENT if state else "", "")
        raise AssertionError(f"unexpected git call {args!r}")


def _asked(root: str) -> list[tuple[tuple[str, ...], object]]:
    """Every git question a full pass asks, in order: the clerk checkout, the base, both probes."""
    return [
        (("describe", "--tags", "--dirty", "--always"), root),
        (("merge-base", "HEAD", "refs/remotes/origin/main"), None),
        (("ls-tree", "-z", SHA, "--", "clerk.toml"), None),
        (("ls-tree", "-z", "refs/remotes/origin/main", "--", "clerk.toml"), None),
    ]


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeGit, exits: list[int]) -> list[list[str]]:
    runs: list[list[str]] = []

    def fake_run(argv: list[str]) -> int:
        runs.append(argv)
        return exits.pop(0)

    monkeypatch.setattr(g, "_git", fake)
    monkeypatch.setattr(g, "_run", fake_run)
    return runs


@pytest.mark.parametrize(
    ("state", "expected"),
    [(True, True), (False, False), (None, None)],
    ids=["present", "absent", "git-error"],
)
def test_has_clerk_config_never_reads_an_error_as_absent(
    monkeypatch: pytest.MonkeyPatch, state: bool | None, expected: bool | None
) -> None:
    fake = _FakeGit(at_base=state)
    monkeypatch.setattr(g, "_git", fake)
    assert g._has_clerk_config(SHA) is expected
    assert fake.calls == [(("ls-tree", "-z", SHA, "--", "clerk.toml"), None)]


def test_clerk_base_sha_requires_full_hex_sha(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakeGit()
    _patch(monkeypatch, fake, [])
    assert g.clerk_base_sha() == SHA
    assert fake.calls == [(("merge-base", "HEAD", "refs/remotes/origin/main"), None)]
    assert capsys.readouterr().out == ""
    for bad in (SHA[:12], "g" * 40, SHA + "0"):
        _patch(monkeypatch, _FakeGit(sha=bad), [])
        assert g.clerk_base_sha() is None, bad
        assert f"local gate: unexpected merge-base output {bad[:80]!r}" in capsys.readouterr().out


def test_clerk_base_sha_says_why_git_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stderr = "fatal: Not a valid object name refs/remotes/origin/main\nhint: second line"
    _patch(monkeypatch, _FakeGit(merge_base=128, merge_base_stderr=stderr), [])
    assert g.clerk_base_sha() is None
    out = capsys.readouterr().out
    assert (
        "local gate: git merge-base exit 128: fatal: Not a valid object name"
        " refs/remotes/origin/main" in out
    )
    assert "second line" not in out


def test_run_clerk_gate_non_directory_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("a bad root must be refused before git or the clerk is touched")

    monkeypatch.setattr(g, "_git", forbidden)
    monkeypatch.setattr(g, "_run", forbidden)
    assert g.run_clerk_gate(str(tmp_path / "missing")) == 2
    assert "is not a directory" in capsys.readouterr().out


def test_run_clerk_gate_no_merge_base_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _patch(monkeypatch, _FakeGit(merge_base=128, merge_base_stderr="fatal: no such ref"), [])
    assert g.run_clerk_gate(str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert runs == [] and "no merge-base with origin/main" in out
    assert "git merge-base exit 128: fatal: no such ref" in out


@pytest.mark.parametrize(
    ("at_base", "at_origin", "where"),
    [
        (None, False, "at base"),
        (None, True, "at base"),
        (False, None, "at origin/main"),
        (True, None, "at origin/main"),
    ],
)
def test_run_clerk_gate_git_error_on_config_probe_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    at_base: bool | None,
    at_origin: bool | None,
    where: str,
) -> None:
    runs = _patch(monkeypatch, _FakeGit(at_base=at_base, at_origin=at_origin), [])
    assert g.run_clerk_gate(str(tmp_path)) == 2
    assert runs == [], "a git error is an input error, never 'absent' (which would bootstrap)"
    assert f"git could not read clerk.toml {where} — input error" in capsys.readouterr().out


def test_run_clerk_gate_refuses_stale_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakeGit(at_base=False, at_origin=True)
    runs = _patch(monkeypatch, fake, [])
    assert g.run_clerk_gate(str(tmp_path)) == 2
    assert runs == [] and "rebase onto origin/main" in capsys.readouterr().out
    assert fake.calls == _asked(str(tmp_path))


def test_run_clerk_gate_bootstrap_runs_hygiene_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakeGit(at_base=False, at_origin=False)
    runs = _patch(monkeypatch, fake, [0])
    assert g.run_clerk_gate(str(tmp_path)) == 0
    assert runs == [g.clerk_gate_command(str(tmp_path), SHA, mode="bootstrap")]
    out = capsys.readouterr().out
    assert "bootstrap run (--trust-worktree-config)" in out and "v0.1.0" in out
    assert fake.calls == _asked(str(tmp_path))
    # Bootstrap is blocking: a hygiene failure (1) or an input error (2) is returned as is.
    for code in (1, 2):
        runs = _patch(monkeypatch, _FakeGit(at_base=False, at_origin=False), [code])
        assert g.run_clerk_gate(str(tmp_path)) == code
        assert len(runs) == 1, "bootstrap never runs the advisory surfaces gate"


def test_run_clerk_gate_normal_runs_blocking_then_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakeGit()
    runs = _patch(monkeypatch, fake, [0, 1])
    assert g.run_clerk_gate(str(tmp_path)) == 0
    assert runs == [
        g.clerk_gate_command(str(tmp_path), SHA, mode="normal"),
        g.clerk_gate_command(str(tmp_path), SHA, mode="surfaces"),
    ]
    assert "surfaces advisory exit 1 (not blocking)" in capsys.readouterr().out
    assert fake.calls == _asked(str(tmp_path))
    runs = _patch(monkeypatch, _FakeGit(), [1, 0])
    assert g.run_clerk_gate(str(tmp_path)) == 1
    assert len(runs) == 1, "a blocking failure returns before the advisory run"
    # An input error (2) from the blocking run is returned as 2, not normalised to 1.
    runs = _patch(monkeypatch, _FakeGit(), [2])
    assert g.run_clerk_gate(str(tmp_path)) == 2
    assert len(runs) == 1
    # Any advisory exit (including >= 2) stays advisory.
    capsys.readouterr()
    runs = _patch(monkeypatch, _FakeGit(), [0, 2])
    assert g.run_clerk_gate(str(tmp_path)) == 0
    assert len(runs) == 2
    assert "advisory exit 2 (not blocking)" in capsys.readouterr().out


def test_clerk_stage_skips_and_maps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    def fake_gate(root: str) -> int:
        calls.append(root)
        return 2

    monkeypatch.setattr(g, "run_clerk_gate", fake_gate)
    assert g.clerk_stage({}) == 0
    assert calls == [] and "SKIPPING clerk gate" in capsys.readouterr().out
    # The escape hatch wins even when a root is set.
    assert g.clerk_stage({g.CLERK_ROOT_ENV: "/x", g.SKIP_CLERK_ENV: "1"}) == 0
    assert calls == [] and "SKIPPING clerk gate" in capsys.readouterr().out
    assert g.clerk_stage({g.CLERK_ROOT_ENV: "/x"}) == 2
    out = capsys.readouterr().out
    assert calls == ["/x"] and "FAILED at clerk gate (exit 2" in out
    # A blocked author is pointed at the narrow bypass, not at --no-verify.
    assert "bypass only this stage with IDRAA_GATE_SKIP_CLERK=1 (document why)" in out


@pytest.mark.parametrize(
    ("skip_audit", "expected"),
    [(None, ["pre", "pre", "clerk"]), ("1", ["pre", "clerk"])],
    ids=["audit-on", "audit-skipped"],
)
def test_main_runs_clerk_stage_after_prechecks_and_before_gate_steps(
    monkeypatch: pytest.MonkeyPatch, skip_audit: str | None, expected: list[str]
) -> None:
    order: list[str] = []
    stage_env: list[Mapping[str, str]] = []
    ok = subprocess.CompletedProcess(["x"], 0, "", "")

    def fake_precheck(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        order.append("pre")  # uv lock --check, then the SCA audit when it is not skipped
        return ok

    def fake_stage(env: Mapping[str, str]) -> int:
        order.append("clerk")
        stage_env.append(env)
        return 2

    def forbidden_step(label: str, argv: tuple[str, ...]) -> int:
        pytest.fail(f"GATE_STEPS ran: {label}")

    if skip_audit is None:
        monkeypatch.delenv("IDRAA_GATE_SKIP_AUDIT", raising=False)
    else:
        monkeypatch.setenv("IDRAA_GATE_SKIP_AUDIT", skip_audit)
    monkeypatch.setattr(subprocess, "run", fake_precheck)
    monkeypatch.setattr(g, "clerk_stage", fake_stage)
    monkeypatch.setattr(g, "run_step", forbidden_step)
    # The stage's own exit code is returned unchanged, and it sees the real environment.
    assert g.main() == 2
    assert order == expected
    assert stage_env[0] is os.environ
