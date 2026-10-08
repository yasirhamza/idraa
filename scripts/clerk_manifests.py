#!/usr/bin/env python3
"""Build `.clerk/manifests/citations.json` from the `file:line` citations in the governed docs.

Adoption design 2026-10-03, §3.3. Every backticked `file:line`, `file:first-last` or
`file:a,b-c` citation in the governed documents, outside fenced code blocks, becomes one
manifest entry PER RANGE for the clerk's `citations` gate, with the anchor line's text as its
pattern. Rules:

- recognition: fenced blocks are blanked and an unclosed fence is an error; backtick pairs are
  matched within each blank-line-delimited block, and a block with an odd backtick count is an
  error (one stray backtick must not shift later pairs), and the citations the pairs select must
  equal those a per-line scan finds (a span butting against a citation is an error);
  a bare continuation such as `:2237` is an error
  (name the file); any other pair that looks like a citation (a colon followed by a digit) but
  does not parse is an error naming the form; a pair spanning a line break that looks like a
  citation is an error — nothing citation-like is ever silently dropped;
- resolution: the first tracked path among `<name>`, `src/idraa/<name>`, `fair_cam/<name>`,
  else the unique tracked path ending in `/<name>`; two or more is an error (qualify the name in
  the document); none is an error unless the name is in EXCLUDED_UNTRACKED;
- anchor: the first line of the range whose stripped text has MIN_ANCHOR characters, else the
  first line of the range, which must have MIN_FALLBACK characters; a blank fallback, a first
  line or range end past the end of the file is an error; an anchor that differs from the cited
  first line is reported (the committed manifest carries none: re-cite the range to start there);
  anchors whose pattern occurs more than once in their file are counted (`--verbose` lists them);
- drift guard: entries are keyed by (doc, file, line); `--write` refuses a key whose pattern
  changed AND whose old anchor text is now anchored fewer times in that (doc, file) than before
  (code moved under an unchanged citation) unless `--accept-drift <file>:<line>` names it; a
  re-cite that merely carries an anchor onto a line another citation's anchor held is not drift;
  the baseline is the working-tree manifest, else HEAD's copy, else empty;
- output is deduplicated, sorted, one entry per line, trailing newline; `regex` is always false.

The drift guard is the author's local pre-flight: a commit that deletes the manifest leaves no
HEAD copy to compare against, and the authority is the orchestrator's base-manifest `clerk gate
all` run and the security lane's review of the manifest diff (adoption design §3.3, §5).

`--check` exits 1 when a rule fails or the committed manifest differs from a fresh build;
`--write` rewrites it. Excluded untracked names are always printed (stdout); a shifted anchor and a
drifted site are refusals, listed together on stderr before the closing message.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS: tuple[str, ...] = (
    "docs/security/threat-model.md",
    "docs/reference/fair-departures-register.md",
)
MANIFEST = ".clerk/manifests/citations.json"
# Deployment config is deliberately untracked in the public repo (owner decision 2026-07-23).
EXCLUDED_UNTRACKED: frozenset[str] = frozenset({"fly.toml"})
MIN_ANCHOR = 12
MIN_FALLBACK = 4

_CITE = re.compile(
    r"`([A-Za-z0-9_.][A-Za-z0-9_/.\-]*\.(?:py|toml|yml|yaml|html|md|txt|json|ini|cfg|js|css|sh|sql))"
    r":([0-9][0-9,\-]*)`"
)
_BARE = re.compile(r"`:[0-9][0-9,\-]*`")
_PAIR = re.compile(r"`[^`]*`")
_CITE_LIKE = re.compile(r":\s*\d")
_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_GIT = object()  # sentinel: main() asks git for HEAD's manifest


class ManifestError(Exception):
    """A citation the generator refuses to turn into an entry."""


@dataclass(frozen=True, order=True)
class Entry:
    doc: str
    file: str
    line: int
    pattern: str

    def as_json(self) -> dict[str, object]:
        return {
            "doc": self.doc,
            "file": self.file,
            "line": self.line,
            "pattern": self.pattern,
            "regex": False,
        }


@dataclass
class Build:
    entries: list[Entry]
    excluded: list[str] = field(default_factory=list)
    shifted: list[str] = field(default_factory=list)
    repeated: list[str] = field(default_factory=list)


def _scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def tracked_files(root: Path) -> frozenset[str]:
    """`git ls-files` at `root`, with every GIT_* variable scrubbed (the pre-push hook leaks GIT_DIR)."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True, env=_scrubbed_env()
    )
    return frozenset(p for p in out.stdout.decode("utf-8").split("\0") if p)


def head_manifest_text(root: Path) -> str | None:
    """HEAD's copy of the manifest, or None when HEAD has none."""
    proc = subprocess.run(  # noqa: S603 — argv is constants
        ["git", "show", f"HEAD:{MANIFEST}"],
        cwd=root,
        capture_output=True,
        check=False,
        env=_scrubbed_env(),
    )
    return proc.stdout.decode("utf-8") if proc.returncode == 0 else None


def resolve(cited: str, tracked: frozenset[str]) -> str | None:
    for candidate in (cited, f"src/idraa/{cited}", f"fair_cam/{cited}"):
        if candidate in tracked:
            return candidate
    suffix = sorted(p for p in tracked if p.endswith("/" + cited))
    if len(suffix) == 1:
        return suffix[0]
    if not suffix:
        return None
    raise ManifestError(
        f"ambiguous citation {cited!r}: {', '.join(suffix)}; qualify the citation with its directory"
    )


def ranges(spec: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for item in spec.split(","):
        match = _RANGE.fullmatch(item)
        first = int(match.group(1)) if match else 0
        last = int(match.group(2)) if match and match.group(2) else first
        if match is None or first < 1 or last < first:
            raise ManifestError(f"malformed line range {item!r}")
        out.append((first, last))
    return out


def lines_of(text: str) -> list[str]:
    """Lines numbered as editors and the clerk number them; a trailing newline adds no line."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def masked_lines(lines: list[str]) -> list[str]:
    """The same lines with every fenced code block (fence lines included) blanked."""
    out: list[str] = []
    opened: int | None = None
    for number, line in enumerate(lines, 1):
        if _FENCE.match(line):
            opened = None if opened is not None else number
            out.append("")
        else:
            out.append("" if opened is not None else line)
    if opened is not None:
        raise ManifestError(f"unclosed fenced block opened at line {opened}")
    return out


def anchor(lines: list[str], first: int, last: int) -> tuple[int, str]:
    count = len(lines)
    if first > count:
        raise ManifestError(f"line {first} is past the end of the file ({count} lines)")
    if last > count:
        raise ManifestError(f"range end {last} is past the end of the file ({count} lines)")
    for number in range(first, last + 1):
        text = lines[number - 1].strip()
        if len(text) >= MIN_ANCHOR:
            return number, text
    text = lines[first - 1].strip()
    if not text:
        raise ManifestError(f"line {first} is blank")
    if len(text) < MIN_FALLBACK:
        raise ManifestError(f"line {first} anchor {text!r} is too short to pin")
    return first, text


def _read_lines(path: Path) -> list[str]:
    return lines_of(path.read_bytes().decode("utf-8"))


def _safe_path(root: Path, rel: str) -> Path:
    """`root / rel` only if no component under `root` is a symlink and the target, if it exists,
    is a regular file; the generator never follows a path out of the repository."""
    path = root
    for part in Path(rel).parts:
        path = path / part
        if path.is_symlink():
            raise ManifestError(f"{rel} goes through a symlink ({path.relative_to(root)}); refused")
    if path.exists() and not stat.S_ISREG(os.stat(path).st_mode):
        raise ManifestError(f"{rel} is not a regular file; refused")
    return path


def _blocks(lines: list[str]) -> list[tuple[int, str]]:
    """(first line number, text) of every blank-line-delimited block; code spans never cross blocks."""
    out: list[tuple[int, str]] = []
    start: int | None = None
    for number, line in enumerate([*lines, ""], 1):
        if line.strip():
            if start is None:
                start = number
        elif start is not None:
            out.append((start, "\n".join(lines[start - 1 : number - 1])))
            start = None
    return out


def build(root: Path, docs: Iterable[str], tracked: frozenset[str]) -> Build:
    entries: set[Entry] = set()
    excluded: set[str] = set()
    shifted: list[str] = []
    repeated: set[str] = set()
    errors: list[str] = []
    cache: dict[str, list[str]] = {}
    for doc in docs:
        try:
            try:
                raw = _read_lines(_safe_path(root, doc))
            except OSError:
                raise ManifestError(f"cannot read {doc}") from None
            lines = masked_lines(raw)
        except ManifestError as exc:
            errors.append(f"{doc}: {exc}")
            continue
        paired: set[tuple[int, int, str]] = set()
        odd_lines: set[int] = set()
        for start, block in _blocks(lines):
            # backticks are paired in order within one block; an odd count would shift every later pair
            if block.count("`") % 2:
                errors.append(
                    f"{doc}:{start}: unpaired backtick in this block; balance the backticks"
                )
                odd_lines.update(range(start, start + block.count("\n") + 1))
                continue
            for pair in _PAIR.finditer(block):
                text = pair.group(0)
                if not _CITE_LIKE.search(text):
                    continue
                number = start + block.count("\n", 0, pair.start())
                if "\n" not in text and (_CITE.fullmatch(text) or _BARE.fullmatch(text)):
                    column = pair.start() - (block.rfind("\n", 0, pair.start()) + 1)
                    paired.add((number, column, text))
                if "\n" in text:
                    errors.append(
                        f"{doc}:{number}: citation wrapped across lines; keep it on one line"
                    )
                elif _BARE.fullmatch(text):
                    errors.append(
                        f"{doc}:{number}: bare continuation citation {text}; name the file"
                    )
                elif not _CITE.fullmatch(text):
                    errors.append(
                        f"{doc}:{number}: unrecognised citation form {text}; write it as `file:line`"
                    )
        scanned = {
            (number, match.start(), match.group(0))
            for number, line in enumerate(lines, 1)
            if number not in odd_lines
            for pattern in (_CITE, _BARE)
            for match in pattern.finditer(line)
        }
        for number in sorted({n for n, _, _ in paired ^ scanned}):
            errors.append(
                f"{doc}:{number}: backtick pairing is ambiguous here; separate the code spans with a space"
                " and write citations in single backticks"
            )
        for number, line in enumerate(lines, 1):
            for match in _CITE.finditer(line):
                cited = match.group(1)
                try:
                    file = resolve(cited, tracked)
                    if file is None:
                        if cited in EXCLUDED_UNTRACKED:
                            excluded.add(cited)
                            continue
                        raise ManifestError(f"{cited!r} is not a tracked file")
                    if file not in cache:
                        try:
                            cache[file] = _read_lines(_safe_path(root, file))
                        except OSError:
                            raise ManifestError(f"cannot read {file}") from None
                    cited_lines = cache[file]
                    for first, last in ranges(match.group(2)):
                        at, pattern = anchor(cited_lines, first, last)
                        if at != first:
                            shifted.append(f"{doc}:{number}: {cited}:{first} anchored at :{at}")
                        hits = sum(pattern in text for text in cited_lines)
                        if hits > 1:
                            repeated.add(
                                f"{doc}:{number}: {cited}:{first} pattern {pattern!r} occurs {hits} times in the file"
                            )
                        entries.add(Entry(doc, file, at, pattern))
                except ManifestError as exc:
                    errors.append(f"{doc}:{number}: {exc}")
    if errors:
        raise ManifestError("\n".join(errors))
    return Build(sorted(entries), sorted(excluded), shifted, sorted(repeated))


def render(entries: list[Entry]) -> str:
    body = ",\n".join("  " + json.dumps(e.as_json(), ensure_ascii=False) for e in entries)
    return '{\n "entries": [\n' + body + "\n ]\n}\n"


def parse_manifest(text: str) -> list[Entry]:
    try:
        data = json.loads(text)
        return [
            Entry(str(e["doc"]), str(e["file"]), int(e["line"]), str(e["pattern"]))
            for e in data["entries"]
        ]
    except (ValueError, KeyError, TypeError):
        raise ManifestError(
            f"{MANIFEST} is not a valid citations manifest; restore it with"
            f" `git checkout HEAD -- {MANIFEST}`, never delete it"
        ) from None


def load_baseline(root: Path, head_text: str | None) -> list[Entry]:
    """The drift baseline: the working-tree manifest if present and valid, else HEAD's copy, else empty."""
    path = _safe_path(root, MANIFEST)
    if path.is_file():
        try:
            return parse_manifest(path.read_text(encoding="utf-8"))
        except ManifestError:
            if head_text is None:
                raise
    return parse_manifest(head_text) if head_text is not None else []


def drift(
    before: list[Entry], after: list[Entry], accepted: set[str]
) -> tuple[list[str], list[str]]:
    """(drifted messages, unused acceptances). A drifted key: code moved under an unchanged citation.

    The rule is multiplicity, not key identity. A key `(doc, file, line)` whose pattern changed is
    drift ONLY when the old anchor text is now anchored fewer times than before within the same
    `(doc, file)`: the text the citation pinned has vanished from the anchored set. If the old text
    is still anchored as often (just at another line), the anchor merely moved. That is the re-cite
    collision: one line inserted above `services/auth.py:27` and every citation correctly re-cited,
    so the new anchor line `28` previously held another citation's anchor. Refusing it would leave
    `--accept-drift`, a false record, as the only exit. A real drift, a forgotten re-cite and a
    partial re-cite all lose an anchor and stay refused; two cited lines swapped under unchanged
    citations is the residual that only the base-manifest `clerk gate` run reports.
    """
    old = {(e.doc, e.file, e.line): e.pattern for e in before}
    had = Counter((e.doc, e.file, e.pattern) for e in before)
    kept = Counter((e.doc, e.file, e.pattern) for e in after)
    drifted: set[str] = set()
    used: set[str] = set()
    for e in after:
        key = (e.doc, e.file, e.line)
        if key in old and old[key] != e.pattern:
            moved = (e.doc, e.file, old[key])
            if kept[moved] >= had[moved]:
                continue  # re-cite collision: the old anchor text is still anchored, at a new line
            site = f"{e.file}:{e.line}"
            if site in accepted:
                used.add(site)
            else:
                drifted.add(f"{site} changed under an unchanged citation in {e.doc}")
    return sorted(drifted), sorted(accepted - used)


def main(
    argv: list[str] | None = None,
    *,
    root: Path = REPO_ROOT,
    tracked: frozenset[str] | None = None,
    docs: Iterable[str] = DOCS,
    head_text: object = _GIT,
) -> int:
    parser = argparse.ArgumentParser(
        description="Build the clerk citations manifest from the governed documents."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="exit 1 if the committed manifest is stale"
    )
    mode.add_argument("--write", action="store_true", help="rewrite the manifest")
    parser.add_argument(
        "--accept-drift",
        action="append",
        default=[],
        metavar="FILE:LINE",
        help="allow --write to re-anchor this site although the code under the citation changed",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="list anchors whose pattern repeats in their file"
    )
    args = parser.parse_args(argv)
    if args.accept_drift and not args.write:
        parser.error("--accept-drift is only valid with --write")
    if tracked is None:
        tracked = tracked_files(root)
    head = head_manifest_text(root) if head_text is _GIT else head_text
    try:
        path = _safe_path(root, MANIFEST)
        built = build(root, docs, tracked)
        baseline = load_baseline(root, head if isinstance(head, str) else None)
    except ManifestError as exc:
        print(f"clerk-manifests: {exc}", file=sys.stderr)
        return 1
    for name in built.excluded:
        print(f"clerk-manifests: excluded (untracked): {name}")
    drifted, unused = drift(baseline, built.entries, set(args.accept_drift))
    if built.shifted:
        for note in built.shifted:
            print(f"clerk-manifests: shifted anchor: {note}", file=sys.stderr)
        for item in drifted:
            print(f"clerk-manifests: {item}", file=sys.stderr)
        print(
            "clerk-manifests: a citation must start on its anchor line; re-cite each shifted range in the"
            " document" + (" and each drifted site" if drifted else ""),
            file=sys.stderr,
        )
        return 1
    if built.repeated:
        print(
            f"clerk-manifests: {len(built.repeated)} anchors repeat in their file (informational; --verbose lists them)"
        )
        if args.verbose:
            for note in built.repeated:
                print(f"clerk-manifests: repeated pattern: {note}")
    for site in unused:
        print(
            f"clerk-manifests: --accept-drift {site} matched no drifted citation", file=sys.stderr
        )
    text = render(built.entries)
    if args.write:
        if drifted:
            for item in drifted:
                print(f"clerk-manifests: {item}", file=sys.stderr)
            print(
                "clerk-manifests: re-cite in the document; pass --accept-drift <file>:<line> only if the"
                " cited claim still holds",
                file=sys.stderr,
            )
            return 1
        for site in sorted(set(args.accept_drift) - set(unused)):
            print(
                f"clerk-manifests: record in the commit and PR bodies: accept-drift {site} (claim: <section>)"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"clerk-manifests: wrote {len(built.entries)} entries to {MANIFEST}")
        return 0
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if current != text:
        if drifted:
            for item in drifted:
                print(f"clerk-manifests: {item}", file=sys.stderr)
            print(
                f"clerk-manifests: {MANIFEST} is stale: fix the citation in the document first",
                file=sys.stderr,
            )
        else:
            print(
                f"clerk-manifests: {MANIFEST} is stale: document citations changed — run"
                " `uv run python scripts/clerk_manifests.py --write`",
                file=sys.stderr,
            )
        return 1
    print(f"clerk-manifests: {MANIFEST} is fresh ({len(built.entries)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
