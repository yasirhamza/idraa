"""Tracked-path denylist guard (outbound-leak control).

Fails if any *tracked* file matches a denylisted pattern: local tool/agent
state, secrets-shaped files, databases, keystores, or the private archive's
internal doc trees. Complements gitleaks (which scans *content*): this catches
the "committed the whole state dir / keystore / db" accident that content
scanning can miss, and stops the private archive's internal docs from being
re-imported into the public tree.

Runs as a pre-commit hook (always_run) and is cheap: one `git ls-files` pass.
The denylist mirrors .gitignore's sensitive entries — keep them in sync.
"""

from __future__ import annotations

import fnmatch
import subprocess

# Directory prefixes that must never be tracked. Local tool/agent state only —
# future first-party docs (specs/plans/runbooks) are legitimate tracked content.
DENY_PREFIXES: tuple[str, ...] = (
    ".claude/",
    ".superpowers/",
    ".memsearch/",
    ".design-sync/",
    "docs/memory/",  # committed agent memory is never appropriate
)

# Filename globs that must never be tracked (anywhere in the tree).
DENY_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.db",
    "*.db-journal",
    "*.db-shm",
    "*.db-wal",
    "*.sqlite",
    "*.sqlite3",
    "*.jks",
    "*.keystore",
    "*.pem",
    "*.p12",
    "local.properties",
    # Deployment configuration (owner decision 2026-07-23): platform, VM
    # sizing, and DB-path details are operational disclosures — deploy config
    # stays operator-local, including per-instance variants (fly.<name>.toml).
    "fly.toml",
    "fly.*.toml",
)

# Explicit allowlist: tracked paths that legitimately match a deny glob.
ALLOW: frozenset[str] = frozenset(
    {
        ".env.example",  # documented template, no real values
    }
)


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    offenders: list[str] = []
    for path in tracked:
        if path in ALLOW:
            continue
        if any(path.startswith(prefix) for prefix in DENY_PREFIXES):
            offenders.append(path)
            continue
        basename = path.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(basename, pattern) for pattern in DENY_GLOBS):
            offenders.append(path)

    if offenders:
        print("Denylisted path(s) are tracked — these must never be committed:")
        for path in offenders:
            print(f"  {path}")
        print("Untrack with `git rm --cached <path>` and add to .gitignore.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
