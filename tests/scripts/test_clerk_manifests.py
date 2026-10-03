"""scripts/clerk_manifests.py — the citations manifest generator (spec §3.3)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import clerk_manifests as cm

ROOT = Path(__file__).resolve().parent.parent.parent
APP = "from fastapi import FastAPI\n"


def _tree(tmp_path: Path, files: dict[str, str]) -> frozenset[str]:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return frozenset(files)


def _main(tmp_path: Path, tracked: frozenset[str], *argv: str, head_text: str | None = None) -> int:
    return cm.main(
        list(argv), root=tmp_path, tracked=tracked, docs=["docs/a.md"], head_text=head_text
    )


# ---- resolution --------------------------------------------------------------------------------


def test_resolve_exact_then_src_idraa_then_fair_cam() -> None:
    tracked = frozenset(
        {
            "config.py",
            "src/idraa/config.py",
            "src/idraa/x.py",
            "fair_cam/x.py",
            "fair_cam/composition.py",
        }
    )
    assert cm.resolve("config.py", tracked) == "config.py"
    assert cm.resolve("x.py", tracked) == "src/idraa/x.py"
    assert cm.resolve("composition.py", tracked) == "fair_cam/composition.py"


def test_resolve_unique_suffix() -> None:
    tracked = frozenset({"src/idraa/routes/deps.py", "fair_cam/parameters/industry_calibration.py"})
    assert cm.resolve("deps.py", tracked) == "src/idraa/routes/deps.py"
    assert (
        cm.resolve("industry_calibration.py", tracked)
        == "fair_cam/parameters/industry_calibration.py"
    )


def test_resolve_ambiguous_raises() -> None:
    tracked = frozenset({"src/idraa/routes/auth.py", "src/idraa/services/auth.py"})
    with pytest.raises(
        cm.ManifestError,
        match=r"ambiguous citation 'auth\.py'.*qualify the citation with its directory",
    ):
        cm.resolve("auth.py", tracked)


def test_resolve_untracked_returns_none() -> None:
    assert cm.resolve("fly.toml", frozenset({"src/idraa/app.py"})) is None


# ---- ranges, lines, anchors --------------------------------------------------------------------


def test_ranges_parses_comma_lists() -> None:
    assert cm.ranges("4") == [(4, 4)]
    assert cm.ranges("4-5") == [(4, 5)]
    assert cm.ranges("28,295-311,362-378") == [(28, 28), (295, 311), (362, 378)]
    with pytest.raises(cm.ManifestError, match=r"malformed line range '5-4'"):
        cm.ranges("5-4")
    with pytest.raises(cm.ManifestError, match=r"malformed line range '0'"):
        cm.ranges("0")


def test_lines_of_counts_without_trailing_empty() -> None:
    assert cm.lines_of("x\n") == ["x"]
    assert cm.lines_of("x\ny") == ["x", "y"]
    assert cm.lines_of("") == []


def test_masked_lines_blank_fenced_blocks_keep_numbering() -> None:
    lines = ["a `x.py:1`", "```", "inside `y.py:2`", "```", "b `z.py:3`"]
    assert cm.masked_lines(lines) == ["a `x.py:1`", "", "", "", "b `z.py:3`"]


def test_masked_lines_unclosed_fence_raises() -> None:
    with pytest.raises(cm.ManifestError, match=r"unclosed fenced block opened at line 2"):
        cm.masked_lines(["a", "```", "b `x.py:1`"])


def test_anchor_first_of_several_qualifying_lines() -> None:
    lines = ["abc", "x = compute_a()", "y = compute_bbbbbbbb()"]
    assert cm.anchor(lines, 1, 3) == (2, "x = compute_a()")


def test_anchor_twelve_character_boundary() -> None:
    assert cm.anchor(["a" * 11, "b" * 12], 1, 2) == (2, "b" * 12)
    assert cm.anchor(["a" * 11], 1, 1) == (1, "a" * 11)


def test_anchor_fallback_boundary() -> None:
    assert cm.anchor(["abcd", "wxyz"], 1, 2) == (1, "abcd")
    assert cm.anchor(["abcd", "x"], 1, 1) == (1, "abcd")
    with pytest.raises(cm.ManifestError, match=r"line 1 anchor 'abc' is too short to pin"):
        cm.anchor(["abc", "x"], 1, 1)


def test_anchor_blank_line_raises() -> None:
    with pytest.raises(cm.ManifestError, match=r"line 2 is blank"):
        cm.anchor(["a", "", "b"], 2, 2)


def test_anchor_past_eof_raises() -> None:
    with pytest.raises(cm.ManifestError, match=r"line 9 is past the end of the file \(2 lines\)"):
        cm.anchor(["a", "b"], 9, 12)
    with pytest.raises(
        cm.ManifestError, match=r"range end 12 is past the end of the file \(2 lines\)"
    ):
        cm.anchor(["a", "b"], 1, 12)


# ---- build -------------------------------------------------------------------------------------


def test_build_one_entry_per_range_sorted_across_docs(tmp_path: Path) -> None:
    deps = (
        "import x\n\n\ndef client_ip(request):\n    return None\n"
        + "z\n" * 89
        + "def audit_client_ip(request):\n"
        + "z\n" * 10
    )
    tracked = _tree(
        tmp_path,
        {
            "src/idraa/routes/deps.py": deps,
            "docs/b.md": "`deps.py:4,95-96`\n",
            "docs/a.md": "`routes/deps.py:4`\n",
        },
    )
    built = cm.build(tmp_path, ["docs/b.md", "docs/a.md"], tracked)
    assert built.excluded == [] and built.shifted == []
    assert built.entries == [
        cm.Entry("docs/a.md", "src/idraa/routes/deps.py", 4, "def client_ip(request):"),
        cm.Entry("docs/b.md", "src/idraa/routes/deps.py", 4, "def client_ip(request):"),
        cm.Entry("docs/b.md", "src/idraa/routes/deps.py", 95, "def audit_client_ip(request):"),
    ]
    many = tmp_path / "many"
    body = "".join(f"line number {i:02d} long enough\n" for i in range(1, 13))
    cites = " ".join(f"`app.py:{i}`" for i in (12, 3, 9, 1, 7, 11, 5, 2, 10, 4))
    tracked_many = _tree(many, {"src/idraa/app.py": body, "docs/a.md": cites + "\n"})
    built_many = cm.build(many, ["docs/a.md"], tracked_many)
    assert [e.line for e in built_many.entries] == [1, 2, 3, 4, 5, 7, 9, 10, 11, 12]


def test_build_dedupes_repeated_citations(tmp_path: Path) -> None:
    tracked = _tree(
        tmp_path,
        {"src/idraa/app.py": APP, "docs/a.md": "`src/idraa/app.py:1` and again `app.py:1`.\n"},
    )
    built = cm.build(tmp_path, ["docs/a.md"], tracked)
    assert built.entries == [
        cm.Entry("docs/a.md", "src/idraa/app.py", 1, "from fastapi import FastAPI")
    ]
    assert built.repeated == []


def test_build_accepts_leading_dot_path(tmp_path: Path) -> None:
    tracked = _tree(
        tmp_path,
        {
            ".github/workflows/ci.yml": "name: CI\non: push\n",
            "docs/a.md": "`.github/workflows/ci.yml:2`\n",
        },
    )
    assert cm.build(tmp_path, ["docs/a.md"], tracked).entries == [
        cm.Entry("docs/a.md", ".github/workflows/ci.yml", 2, "on: push")
    ]


def test_build_skips_fenced_blocks(tmp_path: Path) -> None:
    tracked = _tree(
        tmp_path,
        {"src/idraa/app.py": APP, "docs/a.md": "```\nnope.py:7 and `gone.py:9`\n```\n`app.py:1`\n"},
    )
    assert [e.line for e in cm.build(tmp_path, ["docs/a.md"], tracked).entries] == [1]


def test_build_accepts_wrapped_code_span_before_citation(tmp_path: Path) -> None:
    """A prose code span that wraps across a line break must not steal the next citation's backtick."""
    tracked = _tree(
        tmp_path, {"src/idraa/app.py": APP, "docs/a.md": "x `f(a,\n b)` (`app.py:1`)\n"}
    )
    assert cm.build(tmp_path, ["docs/a.md"], tracked).entries == [
        cm.Entry("docs/a.md", "src/idraa/app.py", 1, "from fastapi import FastAPI")
    ]


def test_build_refuses_unpaired_backtick(tmp_path: Path) -> None:
    """One stray backtick must not shift the pairing of later blocks and hide their errors."""
    tracked = _tree(
        tmp_path, {"src/idraa/app.py": APP, "docs/a.md": "a ` b\n\n`app.py:1` and `:2`\n"}
    )
    with pytest.raises(cm.ManifestError) as info:
        cm.build(tmp_path, ["docs/a.md"], tracked)
    assert "docs/a.md:1: unpaired backtick in this block; balance the backticks" in str(info.value)
    assert "docs/a.md:3: bare continuation citation `:2`; name the file" in str(info.value)


def test_build_refuses_pairing_disagreement(tmp_path: Path) -> None:
    """Pairs and the per-line scan must select the same citations, or a real one could be dropped."""
    files = {
        "src/idraa/app.py": APP + "x = 1\n",
        "src/idraa/b.py": APP,
        "docs/a.md": "x `a`b.py:1`app.py:2` y\n",
    }
    tracked = _tree(tmp_path, files)
    with pytest.raises(
        cm.ManifestError,
        match=r"docs/a\.md:1: backtick pairing is ambiguous here; separate the code spans with a space",
    ):
        cm.build(tmp_path, ["docs/a.md"], tracked)
    (tmp_path / "docs/a.md").write_text(
        "- a ` b\n- `:2` c ` d\n", encoding="utf-8"
    )  # two strays in one tight list
    with pytest.raises(cm.ManifestError, match=r"docs/a\.md:2: backtick pairing is ambiguous here"):
        cm.build(tmp_path, ["docs/a.md"], tracked)


def test_build_reports_shifted_anchor(tmp_path: Path) -> None:
    tracked = _tree(tmp_path, {"src/idraa/app.py": "\n)\n" + APP, "docs/a.md": "`app.py:1-3`\n"})
    built = cm.build(tmp_path, ["docs/a.md"], tracked)
    assert built.entries == [
        cm.Entry("docs/a.md", "src/idraa/app.py", 3, "from fastapi import FastAPI")
    ]
    assert built.shifted == ["docs/a.md:1: app.py:1 anchored at :3"]


def test_build_reports_repeated_pattern(tmp_path: Path) -> None:
    tracked = _tree(
        tmp_path,
        {
            "src/idraa/app.py": "gc.collect()\nx = 1\ngc.collect()\n",
            "docs/a.md": "`app.py:1` and `app.py:1`\n",
        },
    )
    assert cm.build(tmp_path, ["docs/a.md"], tracked).repeated == [
        "docs/a.md:1: app.py:1 pattern 'gc.collect()' occurs 2 times in the file"
    ]


def test_build_refuses_bare_continuation(tmp_path: Path) -> None:
    tracked = _tree(tmp_path, {"src/idraa/app.py": APP, "docs/a.md": "`app.py:1` and `:1`.\n"})
    with pytest.raises(
        cm.ManifestError, match=r"docs/a\.md:1: bare continuation citation `:1`; name the file"
    ):
        cm.build(tmp_path, ["docs/a.md"], tracked)


def test_build_refuses_unrecognised_form(tmp_path: Path) -> None:
    tracked = _tree(
        tmp_path, {"src/idraa/app.py": APP, "docs/a.md": "`app.py:1–3` and `app.py: 1`.\n"}
    )
    with pytest.raises(cm.ManifestError) as info:
        cm.build(tmp_path, ["docs/a.md"], tracked)
    assert "docs/a.md:1: unrecognised citation form `app.py:1–3`; write it as `file:line`" in str(
        info.value
    )
    assert "docs/a.md:1: unrecognised citation form `app.py: 1`" in str(info.value)


def test_build_refuses_wrapped_citation(tmp_path: Path) -> None:
    tracked = _tree(
        tmp_path, {"src/idraa/app.py": APP, "docs/a.md": "see `src/idraa/app.py:\n1` here\n"}
    )
    with pytest.raises(
        cm.ManifestError, match=r"docs/a\.md:1: citation wrapped across lines; keep it on one line"
    ):
        cm.build(tmp_path, ["docs/a.md"], tracked)


def test_build_excludes_listed_untracked_and_reports(tmp_path: Path) -> None:
    tracked = _tree(
        tmp_path,
        {"docs/a.md": "`fly.toml:27-31` and `src/idraa/app.py:1`.\n", "src/idraa/app.py": APP},
    )
    built = cm.build(tmp_path, ["docs/a.md"], tracked)
    assert built.excluded == ["fly.toml"]
    assert [e.file for e in built.entries] == ["src/idraa/app.py"]


def test_build_unknown_untracked_is_an_error(tmp_path: Path) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`secret.toml:3`.\n"})
    with pytest.raises(
        cm.ManifestError, match=r"docs/a\.md:1: 'secret\.toml' is not a tracked file"
    ):
        cm.build(tmp_path, ["docs/a.md"], tracked)


def test_build_unreadable_cited_file_is_an_error(tmp_path: Path) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/gone.py:1`.\n"}) | {"src/idraa/gone.py"}
    with pytest.raises(cm.ManifestError, match=r"docs/a\.md:1: cannot read src/idraa/gone\.py"):
        cm.build(tmp_path, ["docs/a.md"], tracked)


# ---- render, baseline, drift, main -------------------------------------------------------------


def test_render_shape() -> None:
    text = cm.render([cm.Entry("docs/a.md", "src/idraa/app.py", 1, "from fastapi import FastAPI")])
    assert json.loads(text) == {
        "entries": [
            {
                "doc": "docs/a.md",
                "file": "src/idraa/app.py",
                "line": 1,
                "pattern": "from fastapi import FastAPI",
                "regex": False,
            }
        ]
    }
    assert text.endswith("\n") and text.count("\n") == 5  # {, "entries": [, the entry, ], }


def test_parse_manifest_rejects_garbage() -> None:
    with pytest.raises(cm.ManifestError, match=r"not a valid citations manifest.*git checkout"):
        cm.parse_manifest("<<<<<<< HEAD\n{}\n")


def test_check_and_write_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:1`\n", "src/idraa/app.py": APP})
    assert _main(tmp_path, tracked, "--check") == 1
    assert "document citations changed" in capsys.readouterr().err
    assert _main(tmp_path, tracked, "--write") == 0
    assert (tmp_path / cm.MANIFEST).is_file()
    assert _main(tmp_path, tracked, "--check") == 0
    assert "is fresh (1 entries)" in capsys.readouterr().out


def test_write_refuses_drift_without_accept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:1`\n", "src/idraa/app.py": APP})
    assert _main(tmp_path, tracked, "--write") == 0
    (tmp_path / "src/idraa/app.py").write_text(
        "import logging\n" + APP, encoding="utf-8"
    )  # code moved under the citation
    assert _main(tmp_path, tracked, "--write") == 1
    err = capsys.readouterr().err
    assert (
        "src/idraa/app.py:1 changed under an unchanged citation in docs/a.md" in err
        and "--accept-drift" in err
    )
    assert _main(tmp_path, tracked, "--check") == 1
    assert "fix the citation in the document first" in capsys.readouterr().err
    assert _main(tmp_path, tracked, "--write", "--accept-drift", "src/idraa/app.py:1") == 0
    assert (
        "record in the commit and PR bodies: accept-drift src/idraa/app.py:1 (claim: <section>)"
        in capsys.readouterr().out
    )
    assert (
        json.loads((tmp_path / cm.MANIFEST).read_text())["entries"][0]["pattern"]
        == "import logging"
    )


def test_write_uses_head_baseline_when_working_copy_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:1`\n", "src/idraa/app.py": APP})
    assert _main(tmp_path, tracked, "--write") == 0
    committed = (tmp_path / cm.MANIFEST).read_text(encoding="utf-8")
    (tmp_path / cm.MANIFEST).unlink()  # the "delete it and regenerate" reflex
    (tmp_path / "src/idraa/app.py").write_text("import logging\n" + APP, encoding="utf-8")
    assert _main(tmp_path, tracked, "--write", head_text=committed) == 1
    assert "changed under an unchanged citation" in capsys.readouterr().err
    assert _main(tmp_path, tracked, "--write", head_text=None) == 0, (
        "no HEAD copy either: nothing to compare against"
    )


def test_write_uses_head_baseline_when_working_copy_conflicted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:1`\n", "src/idraa/app.py": APP})
    assert _main(tmp_path, tracked, "--write") == 0
    committed = (tmp_path / cm.MANIFEST).read_text(encoding="utf-8")
    (tmp_path / cm.MANIFEST).write_text(
        "<<<<<<< HEAD\n" + committed, encoding="utf-8"
    )  # a half-merged file
    (tmp_path / "src/idraa/app.py").write_text("import logging\n" + APP, encoding="utf-8")
    assert _main(tmp_path, tracked, "--write", head_text=committed) == 1
    assert "changed under an unchanged citation" in capsys.readouterr().err
    assert _main(tmp_path, tracked, "--write", head_text=None) == 1
    assert "not a valid citations manifest" in capsys.readouterr().err


def test_unused_accept_drift_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:1`\n", "src/idraa/app.py": APP})
    assert _main(tmp_path, tracked, "--write", "--accept-drift", "src/idraa/app.py:99") == 0
    assert (
        "--accept-drift src/idraa/app.py:99 matched no drifted citation" in capsys.readouterr().err
    )


def test_check_refuses_unclosed_fence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tracked = _tree(tmp_path, {"src/idraa/app.py": APP, "docs/a.md": "```\n`app.py:1`\n"})
    assert _main(tmp_path, tracked, "--check") == 1
    assert "docs/a.md: unclosed fenced block opened at line 1" in capsys.readouterr().err


def test_check_reports_rule_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:7`\n", "src/idraa/app.py": "x\n"})
    assert _main(tmp_path, tracked, "--check") == 1
    assert "docs/a.md:1: line 7 is past the end of the file (1 lines)" in capsys.readouterr().err


def test_accept_drift_is_scoped_to_its_site(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One acceptance must not clear a second drifted site."""
    files = {
        "docs/a.md": "`src/idraa/app.py:1` and `src/idraa/b.py:1`\n",
        "src/idraa/app.py": APP,
        "src/idraa/b.py": APP,
    }
    tracked = _tree(tmp_path, files)
    assert _main(tmp_path, tracked, "--write") == 0
    (tmp_path / "src/idraa/app.py").write_text("import logging\n" + APP, encoding="utf-8")
    (tmp_path / "src/idraa/b.py").write_text("import logging\n" + APP, encoding="utf-8")
    assert _main(tmp_path, tracked, "--write", "--accept-drift", "src/idraa/app.py:1") == 1
    assert "src/idraa/b.py:1 changed under an unchanged citation" in capsys.readouterr().err
    same = tmp_path / "same-file"
    two = "first long anchor line\nsecond long anchor line\n"
    tracked_same = _tree(
        same, {"docs/a.md": "`src/idraa/c.py:1` and `src/idraa/c.py:2`\n", "src/idraa/c.py": two}
    )
    assert _main(same, tracked_same, "--write") == 0
    (same / "src/idraa/c.py").write_text(
        "changed first anchor line\nchanged second anchor line\n", encoding="utf-8"
    )
    assert _main(same, tracked_same, "--write", "--accept-drift", "src/idraa/c.py:1") == 1
    assert "src/idraa/c.py:2 changed under an unchanged citation" in capsys.readouterr().err


def test_build_refuses_symlinked_cited_file(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("TOP-SECRET-LINE-0123456789\n", encoding="utf-8")
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:1`\n"}) | {"src/idraa/app.py"}
    (tmp_path / "src/idraa").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src/idraa/app.py").symlink_to(outside)
    with pytest.raises(
        cm.ManifestError, match=r"docs/a\.md:1: src/idraa/app\.py goes through a symlink"
    ):
        cm.build(tmp_path, ["docs/a.md"], tracked)
    linked = tmp_path / "linked-doc"
    outside_doc = tmp_path.parent / f"{tmp_path.name}-outside-doc.md"
    outside_doc.write_text("`app.py:1`\n", encoding="utf-8")
    (linked / "docs").mkdir(parents=True)
    (linked / "docs/a.md").symlink_to(outside_doc)
    with pytest.raises(cm.ManifestError, match=r"docs/a\.md: docs/a\.md goes through a symlink"):
        cm.build(linked, ["docs/a.md"], frozenset({"docs/a.md"}))
    fifo = tmp_path / "fifo"
    tracked_fifo = _tree(fifo, {"docs/a.md": "`src/idraa/app.py:1`\n"}) | {"src/idraa/app.py"}
    (fifo / "src/idraa").mkdir(parents=True)
    os.mkfifo(fifo / "src/idraa/app.py")
    with pytest.raises(
        cm.ManifestError, match=r"docs/a\.md:1: src/idraa/app\.py is not a regular file"
    ):
        cm.build(fifo, ["docs/a.md"], tracked_fifo)


def test_write_refuses_symlinked_manifest_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-dir"
    outside.mkdir()
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:1`\n", "src/idraa/app.py": APP})
    (tmp_path / ".clerk").symlink_to(outside, target_is_directory=True)
    assert _main(tmp_path, tracked, "--write") == 1
    assert "goes through a symlink (.clerk)" in capsys.readouterr().err
    assert not (outside / "manifests" / "citations.json").exists()


def test_shifted_anchor_refuses_check_and_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = _tree(tmp_path, {"src/idraa/app.py": "\n)\n" + APP, "docs/a.md": "`app.py:1-3`\n"})
    assert _main(tmp_path, tracked, "--write") == 1
    assert "must start on its anchor line" in capsys.readouterr().err
    assert not (tmp_path / cm.MANIFEST).exists()
    assert _main(tmp_path, tracked, "--check") == 1
    assert "must start on its anchor line" in capsys.readouterr().err


def test_accept_drift_requires_write(tmp_path: Path) -> None:
    tracked = _tree(tmp_path, {"docs/a.md": "`src/idraa/app.py:1`\n", "src/idraa/app.py": APP})
    with pytest.raises(SystemExit) as info:
        _main(tmp_path, tracked, "--check", "--accept-drift", "src/idraa/app.py:1")
    assert info.value.code == 2
