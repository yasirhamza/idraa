"""Read-only loaders for the scenario and control seed libraries (spec §3), plus the git helpers
used to detect changed entries (spec §4.5.2, §4.5.3)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.curation_check.config import REPO_ROOT

SCENARIO_FILES = (
    Path("data/seed_library_entries.json"),
    Path("data/seed_library_entries_extension.json"),
)
CONTROL_FILE = Path("data/seed_control_library_entries.json")

# No leading '-' (an option), no ':' (pathspec), no whitespace; '..' is rejected separately below.
REF_RE = re.compile(r"[A-Za-z0-9._/~^@{}][A-Za-z0-9._/~^@{}-]*")
_REF_MAX_LEN = 200


@dataclass(frozen=True)
class ScenarioItem:
    slug: str
    name: str
    description: str
    status: str
    threat_event_type: str
    asset_class: str
    threat_actor_type: str

    def state(self) -> dict[str, str]:
        return {"scenario_name": self.name, "scenario_description": self.description}


@dataclass(frozen=True)
class ControlItem:
    slug: str
    name: str
    description: str
    status: str
    functions: frozenset[str]
    dropped: dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    def state(self) -> dict[str, str]:
        return {"control_name": self.name, "control_description": self.description}


def load_scenarios(root: Path = REPO_ROOT) -> list[ScenarioItem]:
    items: list[ScenarioItem] = []
    for rel in SCENARIO_FILES:
        for e in json.loads((root / rel).read_text(encoding="utf-8")):
            items.append(
                ScenarioItem(
                    slug=e["slug"],
                    name=e["name"],
                    description=" ".join(str(e["description"]).split()),
                    status=e["status"],
                    threat_event_type=str(e["threat_event_type"]),
                    asset_class=str(e["asset_class"]),
                    threat_actor_type=str(e["threat_actor_type"]),
                )
            )
    return items


def load_controls(root: Path = REPO_ROOT) -> list[ControlItem]:
    data = json.loads((root / CONTROL_FILE).read_text(encoding="utf-8"))
    dropped: dict[str, dict[str, str]] = {}
    for drop in data.get("_meta", {}).get("claim_drops", []):
        for fn in drop["dropped"]:
            per_slug = dropped.setdefault(drop["slug"], {})
            if fn in per_slug:
                raise ValueError(f"claim_drops lists {drop['slug']!r} / {fn!r} twice")
            per_slug[fn] = drop["reason"]
    return [
        ControlItem(
            slug=e["slug"],
            name=e["name"],
            description=" ".join(str(e["description"]).split()),
            status=e["status"],
            functions=frozenset(a["sub_function"] for a in e["assignments"]),
            dropped=dropped.get(e["slug"], {}),
        )
        for e in data["entries"]
    ]


def seed_hashes(root: Path = REPO_ROOT) -> dict[str, str]:
    return {
        str(rel): hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in (*SCENARIO_FILES, CONTROL_FILE)
    }


def match_options(
    scenarios: Iterable[ScenarioItem], exclude_slug: str | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    """Option name → description, and option name → slug. Names must be unique."""
    options: dict[str, str] = {}
    name_to_slug: dict[str, str] = {}
    for s in scenarios:
        if s.slug == exclude_slug:
            continue
        if s.name in name_to_slug:
            raise ValueError(
                f"library entries {name_to_slug[s.name]!r} and {s.slug!r} share the name {s.name!r}; "
                "matching questions need unique names"
            )
        options[s.name] = s.description
        name_to_slug[s.name] = s.slug
    return options, name_to_slug


def _git_env() -> dict[str, str]:
    """A copy of the process environment with every GIT_* key dropped (a scratch caller, or a git
    hook such as the pre-push gate, can leak GIT_DIR/GIT_INDEX_FILE that would point git at the
    wrong repository), then LC_ALL forced to C so git's own messages are never translated (Sec2-3)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    return env


def _git(root: Path, *args: str) -> str:
    """Run git and return its stdout; raise ValueError on a non-zero exit."""
    # Fixed argv (never a shell), the executable is the literal "git", and every path element is
    # either a module constant or the caller-validated `ref`/`base` — S603 is a false positive here.
    result = subprocess.run(  # noqa: S603
        ["git", "-c", "core.fsmonitor=false", *args],
        cwd=root,
        env=_git_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"git {args[0]} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def _git_rc(root: Path, *args: str) -> int:
    """The non-raising sibling of `_git`: returns the exit code instead of raising on non-zero.
    Lets `OSError` (e.g. the git executable missing) propagate."""
    result = subprocess.run(  # noqa: S603 (same rationale as `_git` above)
        ["git", "-c", "core.fsmonitor=false", *args],
        cwd=root,
        env=_git_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result.returncode


@dataclass(frozen=True)
class ChangedSince:
    ref_sha: str
    merge_base: str
    slugs: frozenset[str]  # published (working-tree status) entries whose record changed
    deprecated: frozenset[str]  # changed entries whose CURRENT working-tree status is not
    # "published" (deprecated, or any other non-published status) — never judged, so never
    # in `slugs` (methodology N2: classified by the new record, not by whether it just
    # transitioned, so an added-already-deprecated or still-deprecated-but-edited entry also
    # lands here instead of silently vanishing from the report while still showing in run.json)


def _blob_text(root: Path, base: str, rel: Path) -> str | None:
    """The file's content at `base`, or None when it did not exist there. Absence is detected
    structurally with `ls-tree` first; the `cat-file` stderr match is only a fallback (Sec2-3)."""
    listing = _git(root, "ls-tree", "--name-only", base, "--", rel.as_posix())
    if not listing.strip():
        return None
    try:
        return _git(root, "cat-file", "blob", f"{base}:{rel.as_posix()}")
    except ValueError as e:
        msg = str(e).lower()
        if "does not exist" in msg or "not a valid object" in msg:
            return None
        raise


def _group_claim_drops(meta: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in meta.get("claim_drops", []):
        out.setdefault(row["slug"], []).append(row)
    return out


def _diff_records(
    old_by_slug: dict[str, dict[str, Any]],
    new_by_slug: dict[str, dict[str, Any]],
    changed: set[str],
    deprecated: set[str],
    old_drops: dict[str, list[dict[str, Any]]] | None = None,
    new_drops: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    """A deleted slug (present at the base, absent now) is silently dropped: it never counts as
    changed. Classification is by the CURRENT (working-tree) record's status, not by whether it
    just transitioned (methodology N2): any changed slug whose new status is not "published" goes
    to `deprecated`, never `changed` — an added-already-deprecated entry, an edited-while-still-
    deprecated entry, and a published→deprecated transition are all "not published now", so all
    three are never judged. A published→published edit (added or content-changed) goes to
    `changed`."""
    for slug, new_record in new_by_slug.items():
        old_record = old_by_slug.get(slug)
        same_record = old_record == new_record
        same_drops = (old_drops or {}).get(slug, []) == (new_drops or {}).get(slug, [])
        if same_record and same_drops:
            continue
        if new_record.get("status") != "published":
            deprecated.add(slug)
        else:
            changed.add(slug)


def changed_subjects(root: Path, ref: str) -> ChangedSince:
    """Published-entry slugs whose parsed seed record (or, for a control, its `_meta.claim_drops`
    rows) differs between `git merge-base <ref> HEAD` and the working tree (spec §4.5.2). `ref` is
    validated before any git call; only the three fixed seed-file paths are ever read from a blob."""
    if (
        not REF_RE.fullmatch(ref)
        or ref.startswith("-")
        or ".." in ref
        or ":" in ref
        or len(ref) > _REF_MAX_LEN
    ):
        raise ValueError(f"invalid ref: {ref!r}")

    try:
        sha = _git(
            root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"
        ).strip()
    except ValueError:
        sha = ""
    if not sha:
        raise ValueError(f"unknown ref: {ref!r}")

    try:
        base = _git(root, "merge-base", sha, "HEAD").strip()
    except ValueError:
        base = ""
    if not base:
        raise ValueError(f"no merge base (shallow clone?): {ref!r}")

    changed: set[str] = set()
    deprecated: set[str] = set()

    for rel in SCENARIO_FILES:
        old_text = _blob_text(root, base, rel)
        old_by_slug = {r["slug"]: r for r in json.loads(old_text)} if old_text is not None else {}
        new_by_slug = {r["slug"]: r for r in json.loads((root / rel).read_text(encoding="utf-8"))}
        _diff_records(old_by_slug, new_by_slug, changed, deprecated)

    old_control_text = _blob_text(root, base, CONTROL_FILE)
    old_doc = (
        json.loads(old_control_text)
        if old_control_text is not None
        else {"_meta": {}, "entries": []}
    )
    new_doc = json.loads((root / CONTROL_FILE).read_text(encoding="utf-8"))
    old_controls_by_slug = {r["slug"]: r for r in old_doc.get("entries", [])}
    new_controls_by_slug = {r["slug"]: r for r in new_doc.get("entries", [])}
    _diff_records(
        old_controls_by_slug,
        new_controls_by_slug,
        changed,
        deprecated,
        _group_claim_drops(old_doc.get("_meta", {})),
        _group_claim_drops(new_doc.get("_meta", {})),
    )

    return ChangedSince(
        ref_sha=sha, merge_base=base, slugs=frozenset(changed), deprecated=frozenset(deprecated)
    )
