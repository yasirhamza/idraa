"""The clerk stage of scripts/run_local_gate.py (adoption design §4). Nothing here runs uv, git
or the clerk: `_git`, `_run` and `subprocess.run` are monkeypatched where needed."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import run_local_gate as g

SHA = "b9499b7975084e550cc40a02f8e8cfaf840934a9"
ROOT = "/plugins/superpowers-clerk"
PREFIX = ["uv", "run", "--frozen", "--project", ROOT, "clerk", "gate"]


def test_clerk_skipped_when_root_unset() -> None:
    reason = g.clerk_skip_reason({})
    assert reason is not None and g.CLERK_ROOT_ENV in reason and "CI always skips" in reason


def test_clerk_skipped_by_escape_hatch() -> None:
    reason = g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x", g.SKIP_CLERK_ENV: "1"})
    assert reason is not None and g.SKIP_CLERK_ENV in reason


def test_clerk_runs_when_root_set() -> None:
    assert g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x"}) is None
    assert g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x", g.SKIP_CLERK_ENV: "0"}) is None


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


class _FakeGit:
    """`_git` stand-in: return codes keyed by the git subcommand and, for cat-file, the ref."""

    def __init__(
        self, merge_base: int = 0, at_base: int = 0, at_origin: int = 0, sha: str = SHA
    ) -> None:
        self.rc = {"merge-base": merge_base, "base": at_base, "origin/main": at_origin}
        self.sha = sha
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, cwd: object = None) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[0] == "describe":
            return subprocess.CompletedProcess(args, 0, "v0.1.0\n", "")
        if args[0] == "merge-base":
            return subprocess.CompletedProcess(
                args,
                self.rc["merge-base"],
                self.sha + "\n" if not self.rc["merge-base"] else "",
                "",
            )
        assert args[0] == "cat-file"
        key = "origin/main" if args[2].startswith("origin/main:") else "base"
        return subprocess.CompletedProcess(args, self.rc[key], "", "")


def _patch(monkeypatch: pytest.MonkeyPatch, fake: _FakeGit, exits: list[int]) -> list[list[str]]:
    runs: list[list[str]] = []

    def fake_run(argv: list[str]) -> int:
        runs.append(argv)
        return exits.pop(0)

    monkeypatch.setattr(g, "_git", fake)
    monkeypatch.setattr(g, "_run", fake_run)
    return runs


def test_clerk_base_sha_requires_full_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeGit(), [])
    assert g.clerk_base_sha() == SHA
    _patch(monkeypatch, _FakeGit(sha=SHA[:12]), [])
    assert g.clerk_base_sha() is None
    _patch(monkeypatch, _FakeGit(merge_base=128), [])
    assert g.clerk_base_sha() is None


def test_run_clerk_gate_non_directory_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert g.run_clerk_gate(str(tmp_path / "missing")) == 2
    assert "is not a directory" in capsys.readouterr().out


def test_run_clerk_gate_no_merge_base_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _patch(monkeypatch, _FakeGit(merge_base=128), [])
    assert g.run_clerk_gate(str(tmp_path)) == 0
    assert runs == [] and "no merge-base with origin/main" in capsys.readouterr().out


def test_run_clerk_gate_refuses_stale_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _patch(monkeypatch, _FakeGit(at_base=128, at_origin=0), [])
    assert g.run_clerk_gate(str(tmp_path)) == 2
    assert runs == [] and "rebase onto origin/main" in capsys.readouterr().out


def test_run_clerk_gate_bootstrap_runs_hygiene_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _patch(monkeypatch, _FakeGit(at_base=128, at_origin=128), [0])
    assert g.run_clerk_gate(str(tmp_path)) == 0
    assert runs == [g.clerk_gate_command(str(tmp_path), SHA, mode="bootstrap")]
    out = capsys.readouterr().out
    assert "bootstrap run (--trust-worktree-config)" in out and "v0.1.0" in out


def test_run_clerk_gate_normal_runs_blocking_then_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _patch(monkeypatch, _FakeGit(), [0, 1])
    assert g.run_clerk_gate(str(tmp_path)) == 0
    assert runs == [
        g.clerk_gate_command(str(tmp_path), SHA, mode="normal"),
        g.clerk_gate_command(str(tmp_path), SHA, mode="surfaces"),
    ]
    assert "surfaces advisory exit 1 (not blocking)" in capsys.readouterr().out
    runs = _patch(monkeypatch, _FakeGit(), [1, 0])
    assert g.run_clerk_gate(str(tmp_path)) == 1
    assert len(runs) == 1, "a blocking failure returns before the advisory run"


def test_clerk_stage_skips_and_maps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(g, "run_clerk_gate", lambda root: calls.append(root) or 2)
    assert g.clerk_stage({}) == 0
    assert calls == [] and "SKIPPING clerk gate" in capsys.readouterr().out
    assert g.clerk_stage({g.CLERK_ROOT_ENV: "/x"}) == 2
    assert calls == ["/x"] and "FAILED at clerk gate (exit 2" in capsys.readouterr().out


def test_main_runs_clerk_stage_after_prechecks_and_before_gate_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    ok = subprocess.CompletedProcess(["x"], 0, "", "")
    monkeypatch.delenv("IDRAA_GATE_SKIP_AUDIT", raising=False)
    monkeypatch.setattr(
        g.subprocess, "run", lambda *a, **k: order.append("pre") or ok
    )  # uv lock --check, SCA audit
    monkeypatch.setattr(g, "clerk_stage", lambda env: order.append("clerk") or 1)
    monkeypatch.setattr(g, "run_step", lambda label, argv: pytest.fail(f"GATE_STEPS ran: {label}"))
    assert g.main() == 1
    assert order == ["pre", "pre", "clerk"]
