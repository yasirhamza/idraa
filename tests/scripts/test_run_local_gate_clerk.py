"""The clerk stage of scripts/run_local_gate.py (adoption design §4; plugin-install design
2026-10-09). Nothing here runs uv, git or the clerk: `_git`, `_run` and `subprocess.run` are
monkeypatched where needed, and no test reads the real `~/.claude` (every env dict names a
`CLAUDE_CONFIG_DIR` or an `IDRAA_CLERK_ROOT`; an autouse fixture also redirects HOME)."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
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
PLUGIN_SHA = "d03ea5f36333027dfda07ed3e29d9d02dcc3e6f5"
KEY = "superpowers-clerk@superpowers-clerk"


@pytest.fixture(autouse=True)
def _never_read_the_real_claude_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt and braces: a test that forgets to name a config dir resolves `~/.claude` under an empty HOME."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    monkeypatch.delenv(g.CLAUDE_CONFIG_DIR_ENV, raising=False)
    monkeypatch.delenv(g.CLERK_ROOT_ENV, raising=False)


def _write_registry(config_dir: Path, payload: object) -> Path:
    path = config_dir / "plugins" / "installed_plugins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _install(config_dir: Path, install_path: Path | str, **extra: Any) -> None:
    """Write an `installed_plugins.json` the way Claude Code does, with one user-scope record."""
    record = {"scope": "user", "installPath": str(install_path), "version": "0.1.1", **extra}
    _write_registry(config_dir, {"version": 2, "plugins": {KEY: [record]}})


def _fake_clerk_root(root: Path) -> Path:
    """Make `root` look like a superpowers-clerk checkout to `run_clerk_gate`: it must hold the two
    files `uv run --frozen --project <root>` needs. Nothing in them is read."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'superpowers-clerk'\n", encoding="utf-8"
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return root


def _write_plugin_json(root: Path, payload: object) -> None:
    manifest = root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(payload if isinstance(payload, str) else json.dumps(payload))


def test_clerk_skipped_when_not_installed_and_root_unset(tmp_path: Path) -> None:
    for env in (
        {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)},
        # An exported-but-empty variable is "unset", not a root named "".
        {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path), g.CLERK_ROOT_ENV: ""},
    ):
        reason = g.clerk_skip_reason(env)
        assert reason == (
            "superpowers-clerk not installed (claude plugin install"
            " superpowers-clerk@superpowers-clerk) and IDRAA_CLERK_ROOT unset (CI always skips)"
        )


def test_clerk_skipped_by_escape_hatch(tmp_path: Path) -> None:
    reason = g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x", g.SKIP_CLERK_ENV: "1"})
    assert reason is not None and g.SKIP_CLERK_ENV in reason
    # The hatch wins over an installed plugin too (and is checked before the registry is read).
    _install(tmp_path, tmp_path)
    reason = g.clerk_skip_reason({g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path), g.SKIP_CLERK_ENV: "1"})
    assert reason is not None and g.SKIP_CLERK_ENV in reason


def test_clerk_runs_when_root_set_or_plugin_installed(tmp_path: Path) -> None:
    assert g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x"}) is None
    assert g.clerk_skip_reason({g.CLERK_ROOT_ENV: "/x", g.SKIP_CLERK_ENV: "0"}) is None
    _install(tmp_path, "/somewhere")
    assert g.clerk_skip_reason({g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}) is None


def test_clerk_env_names_and_origin_ref_are_the_documented_literals() -> None:
    # Users export these names by hand; the origin ref must not be an ambiguous short name
    # (a local tag or branch called origin/main shadows it).
    assert (g.CLERK_ROOT_ENV, g.SKIP_CLERK_ENV) == ("IDRAA_CLERK_ROOT", "IDRAA_GATE_SKIP_CLERK")
    assert g.CLAUDE_CONFIG_DIR_ENV == "CLAUDE_CONFIG_DIR"
    assert g.CLERK_PLUGIN_KEY == "superpowers-clerk@superpowers-clerk"
    assert g.CLERK_ORIGIN_REF == "refs/remotes/origin/main"


def test_clerk_surfaces_manifest_is_the_one_clerk_toml_names() -> None:
    cfg = tomllib.loads((g.REPO_ROOT / g.CLERK_CONFIG).read_text(encoding="utf-8"))
    assert g.CLERK_SURFACES_MANIFEST == ".clerk/manifests/surfaces.json"
    assert cfg["gates"]["surfaces_manifest"] == g.CLERK_SURFACES_MANIFEST
    assert cfg["gates"]["citations_manifest"] == g.CLERK_MANIFEST


def test_clerk_root_env_override_wins_over_an_installed_record(tmp_path: Path) -> None:
    _install(tmp_path, "/installed/clerk")
    env = {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path), g.CLERK_ROOT_ENV: "/dev/clerk"}
    assert g.clerk_root(env) == ("/dev/clerk", "IDRAA_CLERK_ROOT override")


def test_clerk_root_reads_the_installed_record_from_claude_config_dir(tmp_path: Path) -> None:
    _install(tmp_path, "/installed/clerk")
    env = {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}
    assert g.clerk_root(env) == ("/installed/clerk", "installed plugin")
    # An empty override is "unset": the installed record is used.
    assert g.clerk_root({**env, g.CLERK_ROOT_ENV: ""}) == ("/installed/clerk", "installed plugin")
    # The record's directory is NOT checked here: run_clerk_gate refuses a stale one (exit 2).
    assert g.clerk_root(env) is not None and not Path("/installed/clerk").is_dir()


def test_clerk_root_defaults_to_dot_claude_under_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert g.clerk_root({}) is None
    _install(tmp_path / ".claude", "/installed/clerk")
    assert g.clerk_root({}) == ("/installed/clerk", "installed plugin")
    # An exported-but-empty CLAUDE_CONFIG_DIR is "unset" too.
    assert g.clerk_root({g.CLAUDE_CONFIG_DIR_ENV: ""}) == ("/installed/clerk", "installed plugin")


def test_clerk_root_takes_the_first_user_scope_record(tmp_path: Path) -> None:
    env = {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}
    # Claude Code files every scope's installs in the one user registry: a project- or local-scope
    # record made while working in another repository must never become the clerk Idraa runs.
    records = [
        {"scope": "project", "installPath": "/project-scope", "gitCommitSha": "b" * 40},
        {"scope": "local", "installPath": "/local-scope"},
        {"installPath": "/no-scope"},
        "not-a-record",
        {"scope": "user", "installPath": "/user-scope", "gitCommitSha": PLUGIN_SHA},
        {"scope": "user", "installPath": "/second-user-scope"},
    ]
    _write_registry(tmp_path, {"plugins": {KEY: records}})
    assert g.clerk_root(env) == ("/user-scope", "installed plugin")
    assert g.clerk_installed_sha(env) == PLUGIN_SHA, "the sha is the user-scope record's"
    # User scope first, other scopes after: still the user-scope record.
    _write_registry(tmp_path, {"plugins": {KEY: [records[4], records[0]]}})
    assert g.clerk_root(env) == ("/user-scope", "installed plugin")


def test_clerk_root_first_user_scope_record_decides_even_when_unusable(tmp_path: Path) -> None:
    # "The first user-scope record", not "the first usable one": a broken user-scope record means
    # not installed, never a silent fall-through to a later record.
    records = [{"scope": "user"}, {"scope": "user", "installPath": "/later"}]
    _write_registry(tmp_path, {"plugins": {KEY: records}})
    env = {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}
    assert g.clerk_root(env) is None and g.clerk_installed_sha(env) is None


_NOT_INSTALLED: list[tuple[str, object]] = [
    ("empty-object", {}),
    ("top-level-list", []),
    ("top-level-string", "just a string"),
    ("plugins-not-a-dict", {"plugins": ["x"]}),
    ("other-plugin-only", {"plugins": {"other@market": [{"installPath": "/o"}]}}),
    ("empty-record-list", {"plugins": {KEY: []}}),
    ("records-not-a-list", {"plugins": {KEY: {"installPath": "/x"}}}),
    ("record-not-a-dict", {"plugins": {KEY: ["/x"]}}),
    ("record-without-install-path", {"plugins": {KEY: [{"scope": "user"}]}}),
    ("install-path-not-a-string", {"plugins": {KEY: [{"scope": "user", "installPath": 7}]}}),
    ("install-path-empty", {"plugins": {KEY: [{"scope": "user", "installPath": ""}]}}),
    ("project-scope-only", {"plugins": {KEY: [{"scope": "project", "installPath": "/p"}]}}),
    (
        "non-user-scopes-only",
        {
            "plugins": {
                KEY: [
                    {"scope": "project", "installPath": "/p"},
                    {"scope": "local", "installPath": "/l"},
                    {"scope": "managed", "installPath": "/m"},
                ]
            }
        },
    ),
    ("record-without-scope", {"plugins": {KEY: [{"installPath": "/x"}]}}),
    ("scope-not-a-string", {"plugins": {KEY: [{"scope": ["user"], "installPath": "/x"}]}}),
    ("malformed-json", "{not json"),
    ("empty-file", ""),
    ("deeply-nested-json", "[" * 100_000),
]


@pytest.mark.parametrize(
    "payload", [p for _, p in _NOT_INSTALLED], ids=[i for i, _ in _NOT_INSTALLED]
)
def test_clerk_root_unusable_registry_means_not_installed(tmp_path: Path, payload: object) -> None:
    _write_registry(tmp_path, payload)
    assert g.clerk_root({g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}) is None
    assert g.clerk_installed_sha({g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}) is None


def test_clerk_root_missing_unreadable_or_undecodable_registry_means_not_installed(
    tmp_path: Path,
) -> None:
    env = {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}
    assert g.clerk_root(env) is None  # no file at all
    registry = _write_registry(tmp_path, "{}")
    registry.unlink()
    registry.mkdir()  # a directory where the file should be: OSError on read
    assert g.clerk_root(env) is None
    registry.rmdir()
    registry.write_bytes(b"\xff\xfe{\x00")  # not UTF-8
    assert g.clerk_root(env) is None
    assert g.clerk_root({g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path / "nonexistent")}) is None


def test_clerk_installed_sha(tmp_path: Path) -> None:
    env = {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}
    _install(tmp_path, "/installed/clerk", gitCommitSha=PLUGIN_SHA)
    assert g.clerk_installed_sha(env) == PLUGIN_SHA
    _install(tmp_path, "/installed/clerk")
    assert g.clerk_installed_sha(env) is None
    for bad in (None, 7, "", ["x"]):
        _install(tmp_path, "/installed/clerk", gitCommitSha=bad)
        assert g.clerk_installed_sha(env) is None, bad


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
        "--manifest",
        g.CLERK_SURFACES_MANIFEST,
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
        described: str = "v0.1.0",
    ) -> None:
        self.described = described
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
            return subprocess.CompletedProcess(args, 0, self.described + "\n", "")
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
    missing = str(tmp_path / "missing")
    assert g.run_clerk_gate(missing) == 2
    assert f"{g.CLERK_ROOT_ENV}={missing!r} is not a directory" in capsys.readouterr().out


def test_run_clerk_gate_stale_installed_record_names_the_path_and_the_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("a stale record must be refused before git or the clerk is touched")

    monkeypatch.setattr(g, "_git", forbidden)
    monkeypatch.setattr(g, "_run", forbidden)
    stale = str(tmp_path / "plugins" / "cache" / "gone")
    assert g.run_clerk_gate(stale, "installed plugin", PLUGIN_SHA) == 2
    out = capsys.readouterr().out
    assert repr(stale) in out and "is not a directory" in out
    assert "claude plugin update superpowers-clerk@superpowers-clerk" in out


def test_clerk_stage_stale_installed_record_exits_2_with_the_record_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(g, "_git", lambda *a, **k: pytest.fail("git touched"))
    monkeypatch.setattr(g, "_run", lambda argv: pytest.fail("clerk run"))
    stale = tmp_path / "plugins" / "cache" / "superpowers-clerk" / "0.0.9"
    _install(tmp_path, stale, gitCommitSha=PLUGIN_SHA)
    assert g.clerk_stage({g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}) == 2
    out = capsys.readouterr().out
    assert repr(str(stale)) in out
    assert "claude plugin update superpowers-clerk@superpowers-clerk" in out
    assert "FAILED at clerk gate (exit 2" in out


@pytest.mark.parametrize("relative", ["clerk", "./clerk", "plugins/cache/clerk", "../clerk"])
def test_run_clerk_gate_relative_installed_record_exits_2(
    relative: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("a relative record must be refused before git or the clerk is touched")

    # A real clerk-shaped directory sits where the relative path resolves from the working
    # directory (the repository under review): it must still be refused, before is_dir().
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _fake_clerk_root((cwd / relative).resolve())
    monkeypatch.chdir(cwd)
    assert (Path(relative) / "uv.lock").is_file(), "the relative path really resolves to a clerk"
    monkeypatch.setattr(g, "_git", forbidden)
    monkeypatch.setattr(g, "_run", forbidden)
    assert g.run_clerk_gate(relative, "installed plugin", PLUGIN_SHA) == 2
    out = capsys.readouterr().out
    assert f"points at {relative!r}, which is not an absolute path" in out
    assert "claude plugin update superpowers-clerk@superpowers-clerk" in out
    assert "is not a directory" not in out and "clerk at" not in out


def test_clerk_stage_relative_installed_record_exits_2_with_the_record_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(g, "_git", lambda *a, **k: pytest.fail("git touched"))
    monkeypatch.setattr(g, "_run", lambda argv: pytest.fail("clerk run"))
    _install(tmp_path, "plugins/cache/superpowers-clerk/0.1.1", gitCommitSha=PLUGIN_SHA)
    assert g.clerk_stage({g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}) == 2
    out = capsys.readouterr().out
    assert "'plugins/cache/superpowers-clerk/0.1.1', which is not an absolute path" in out
    assert "claude plugin update superpowers-clerk@superpowers-clerk" in out
    assert "FAILED at clerk gate (exit 2" in out


def test_run_clerk_gate_relative_override_is_the_developers_own_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The absolute-path rule is for the installed record only; a developer's override may be relative.
    _fake_clerk_root(tmp_path / "clerk")
    monkeypatch.chdir(tmp_path)
    _patch(monkeypatch, _FakeGit(), [0, 0])
    assert g.run_clerk_gate("clerk") == 0


@pytest.mark.parametrize("source", ["IDRAA_CLERK_ROOT override", "installed plugin"])
@pytest.mark.parametrize(
    "present",
    [
        (),
        ("pyproject.toml",),
        ("uv.lock",),
        ("pyproject.toml", "uv.lock/"),
        ("pyproject.toml/", "uv.lock"),
    ],
    ids=["neither", "no-lock", "no-pyproject", "lock-is-a-directory", "pyproject-is-a-directory"],
)
def test_run_clerk_gate_directory_that_is_not_a_clerk_exits_2(
    source: str,
    present: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("a directory that is not a clerk must be refused before git or the clerk runs")

    for name in present:
        if name.endswith("/"):
            (tmp_path / name).mkdir()
        else:
            (tmp_path / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(g, "_git", forbidden)  # no `git describe` in the directory either
    monkeypatch.setattr(g, "_run", forbidden)
    installed = source == "installed plugin"
    assert g.run_clerk_gate(str(tmp_path), source, PLUGIN_SHA if installed else None) == 2
    out = capsys.readouterr().out
    assert f"local gate: {str(tmp_path)!r} has no pyproject.toml/uv.lock" in out
    assert "not a superpowers-clerk checkout" in out
    assert ("claude plugin update superpowers-clerk@superpowers-clerk" in out) is installed
    assert "clerk at" not in out, "no version line for a directory that is not a clerk"


def test_run_clerk_gate_no_merge_base_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_clerk_root(tmp_path)
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
    _fake_clerk_root(tmp_path)
    runs = _patch(monkeypatch, _FakeGit(at_base=at_base, at_origin=at_origin), [])
    assert g.run_clerk_gate(str(tmp_path)) == 2
    assert runs == [], "a git error is an input error, never 'absent' (which would bootstrap)"
    assert f"git could not read clerk.toml {where} — input error" in capsys.readouterr().out


def test_run_clerk_gate_refuses_stale_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_clerk_root(tmp_path)
    fake = _FakeGit(at_base=False, at_origin=True)
    runs = _patch(monkeypatch, fake, [])
    assert g.run_clerk_gate(str(tmp_path)) == 2
    assert (
        runs == [] and "git rebase --onto origin/main <old adoption tip>" in capsys.readouterr().out
    )
    assert fake.calls == _asked(str(tmp_path))


def test_run_clerk_gate_bootstrap_runs_hygiene_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_clerk_root(tmp_path)
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
        assert len(runs) == 1, "bootstrap never runs the surfaces gate"


def test_run_clerk_gate_normal_runs_two_blocking_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_clerk_root(tmp_path)
    fake = _FakeGit()
    runs = _patch(monkeypatch, fake, [0, 0])
    assert g.run_clerk_gate(str(tmp_path)) == 0
    assert runs == [
        g.clerk_gate_command(str(tmp_path), SHA, mode="normal"),
        g.clerk_gate_command(str(tmp_path), SHA, mode="surfaces"),
    ]
    assert "--manifest" in runs[1] and g.CLERK_SURFACES_MANIFEST in runs[1]
    out = capsys.readouterr().out
    assert "== local gate: clerk gate surfaces ==" in out
    assert "advisory" not in out and "not blocking" not in out
    assert fake.calls == _asked(str(tmp_path))
    # A surfaces finding (1) or input error (2) fails the stage with its own exit code.
    for code in (1, 2):
        runs = _patch(monkeypatch, _FakeGit(), [0, code])
        assert g.run_clerk_gate(str(tmp_path)) == code
        assert len(runs) == 2
    # A citations/hygiene failure returns before surfaces runs; 2 is not normalised to 1.
    for code in (1, 2):
        runs = _patch(monkeypatch, _FakeGit(), [code, 0])
        assert g.run_clerk_gate(str(tmp_path)) == code
        assert len(runs) == 1, "a blocking failure returns before the surfaces run"


def test_version_line_installed_plugin_reads_plugin_json_and_appends_the_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_clerk_root(tmp_path)
    _write_plugin_json(tmp_path, {"name": "superpowers-clerk", "version": "0.1.1"})
    fake = _FakeGit()
    _patch(monkeypatch, fake, [0, 0])
    assert g.run_clerk_gate(str(tmp_path), "installed plugin", PLUGIN_SHA) == 0
    assert (
        f"local gate: clerk at {tmp_path} (0.1.1 d03ea5f36333; installed plugin)"
        in capsys.readouterr().out
    )
    assert not [c for c in fake.calls if c[0][0] == "describe"], "no git for the installed copy"


def test_version_line_override_without_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_clerk_root(tmp_path)
    _write_plugin_json(tmp_path, {"version": "0.1.1"})
    _patch(monkeypatch, _FakeGit(), [0, 0])
    assert g.run_clerk_gate(str(tmp_path), "IDRAA_CLERK_ROOT override") == 0
    assert f"clerk at {tmp_path} (0.1.1; IDRAA_CLERK_ROOT override)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        "{not json",
        "[]",
        {},
        {"version": ""},
        {"version": "  "},
        {"version": 1},
        {"version": None},
    ],
    ids=["absent", "malformed", "not-an-object", "no-key", "empty", "blank", "number", "null"],
)
def test_version_line_falls_back_to_git_describe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    manifest: object,
) -> None:
    _fake_clerk_root(tmp_path)
    if manifest is not None:
        _write_plugin_json(tmp_path, manifest)
    fake = _FakeGit(described="v0.1.0-3-gabc1234-dirty")
    _patch(monkeypatch, fake, [0, 0])
    assert g.run_clerk_gate(str(tmp_path), "IDRAA_CLERK_ROOT override") == 0
    assert (
        f"clerk at {tmp_path} (v0.1.0-3-gabc1234-dirty; IDRAA_CLERK_ROOT override)"
        in capsys.readouterr().out
    )
    assert fake.calls[0] == (("describe", "--tags", "--dirty", "--always"), str(tmp_path))


def test_version_line_unknown_when_neither_manifest_nor_git_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_clerk_root(tmp_path)
    _patch(monkeypatch, _FakeGit(described=""), [0, 0])
    assert g.run_clerk_gate(str(tmp_path), "installed plugin", PLUGIN_SHA) == 0
    assert (
        f"clerk at {tmp_path} (unknown d03ea5f36333; installed plugin)" in capsys.readouterr().out
    )


def test_clerk_stage_skips_and_maps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_gate(root: str, source: str, sha: str | None) -> int:
        calls.append((root, source, sha))
        return 2

    monkeypatch.setattr(g, "run_clerk_gate", fake_gate)
    config = {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}
    # Not installed, no override: one skip line naming both ways in.
    assert g.clerk_stage(config) == 0
    skipped = capsys.readouterr().out
    assert calls == [] and "SKIPPING clerk gate" in skipped
    assert "claude plugin install superpowers-clerk@superpowers-clerk" in skipped
    assert "IDRAA_CLERK_ROOT unset" in skipped
    # The escape hatch wins even when a root is set.
    assert g.clerk_stage({g.CLERK_ROOT_ENV: "/x", g.SKIP_CLERK_ENV: "1"}) == 0
    assert calls == [] and "SKIPPING clerk gate" in capsys.readouterr().out
    # Override: the env root, its source label, and no sha.
    assert g.clerk_stage({g.CLERK_ROOT_ENV: "/x"}) == 2
    out = capsys.readouterr().out
    assert (
        calls == [("/x", "IDRAA_CLERK_ROOT override", None)]
        and "FAILED at clerk gate (exit 2" in out
    )
    # A blocked author is pointed at the narrow bypass, not at --no-verify.
    assert "bypass only this stage with IDRAA_GATE_SKIP_CLERK=1 (document why)" in out


def test_clerk_stage_runs_the_installed_plugin_with_its_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_gate(root: str, source: str, sha: str | None) -> int:
        calls.append((root, source, sha))
        return 0

    monkeypatch.setattr(g, "run_clerk_gate", fake_gate)
    installed = tmp_path / "cache" / "0.1.1"
    _install(tmp_path, installed, gitCommitSha=PLUGIN_SHA)
    env = {g.CLAUDE_CONFIG_DIR_ENV: str(tmp_path)}
    assert g.clerk_stage(env) == 0
    assert calls == [(str(installed), "installed plugin", PLUGIN_SHA)]
    # The override wins and never carries the installed record's sha.
    calls.clear()
    assert g.clerk_stage({**env, g.CLERK_ROOT_ENV: "/dev/clerk"}) == 0
    assert calls == [("/dev/clerk", "IDRAA_CLERK_ROOT override", None)]
    # The record without a sha (or with a malformed one) runs with None.
    _install(tmp_path, installed)
    calls.clear()
    assert g.clerk_stage(env) == 0
    assert calls == [(str(installed), "installed plugin", None)]


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
