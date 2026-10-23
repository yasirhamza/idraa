"""Guard: no GitHub workflow touches repository secrets, the clerk judge key, or --external (#220).

CI must never be able to reach the clerk's external judge. The judge key lives in
the operator's macOS Keychain and the judge leaves the machine only on a
``clerk run ... --external`` invocation made by the orchestrator (CLAUDE.md,
Clerk paragraph). A workflow that read a repository secret, named the judge's
keychain item, or passed ``--external`` would open a path from untrusted PR code
to that judge. Today no workflow references ``secrets.`` at all, so the rule is
absolute rather than allowlisted: if a future workflow genuinely needs a secret,
this test must be amended deliberately in the same PR (and reviewed as a
gate-integrity change), not bypassed.

Checked against every ``.github/workflows/*.yml|*.yaml``:

* no ``secrets.`` / ``secrets[`` expression, ``toJSON(secrets)`` or ``secrets: inherit``;
* none of the judge-key identifiers: the keychain item the clerk config names
  (``jev-eval-typesafe-key``), the curation checker's ``idraa-typesafe-key`` family
  and the ``TYPESAFE_API_KEY`` fallback variable;
* no ``--external`` flag.

The identifiers are literals, deliberately NOT read from the clerk's config file:
that would make this test part of the clerk adoption's footprint, which
``tests/arch/test_clerk_isolation.py`` pins to a fixed file list (the rollback
procedure removes exactly those files). The keychain item name is already pinned
by ``tests/scripts/test_clerk_config.py``, so renaming it there fails that test and
the rename gets reviewed together with this list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github" / "workflows"

# Literal judge-key identifiers that must never appear in a workflow.
_JUDGE_KEY_LITERALS: tuple[str, ...] = (
    "jev-eval-typesafe-key",
    "idraa-typesafe-key",
    "TYPESAFE_API_KEY",
)

# `secrets` as an expression root. The lookbehind keeps `needs.secrets.result` and
# `my_secrets.x` out (a job that happens to be called `secrets`, as in ci.yml, is
# not a secret reference); `secrets (gitleaks, full history)` and `secrets:` as a
# job key do not match either.
_SECRETS_REFERENCE = re.compile(
    r"""
    (?<![\w.])secrets\s*[.\[]          # secrets.FOO / secrets['FOO']
    | toJSON\(\s*secrets\s*\)          # toJSON(secrets)
    | ^\s*secrets\s*:\s*inherit\b      # reusable-workflow secret passthrough
    """,
    re.VERBOSE | re.MULTILINE,
)
_EXTERNAL_FLAG = re.compile(r"(?<![\w-])--external(?![\w-])")


def find_violations(text: str, judge_key_names: tuple[str, ...] = _JUDGE_KEY_LITERALS) -> list[str]:
    """Human-readable violations in one workflow's text (empty list = clean)."""
    found: list[str] = []
    if _SECRETS_REFERENCE.search(text):
        found.append("references repository secrets (secrets.* / secrets[...] / inherit)")
    found.extend(
        f"references the judge key identifier {name!r}" for name in judge_key_names if name in text
    )
    if _EXTERNAL_FLAG.search(text):
        found.append("passes --external")
    return found


def _workflow_files() -> list[Path]:
    return sorted([*_WORKFLOWS.glob("*.yml"), *_WORKFLOWS.glob("*.yaml")])


def test_the_guard_actually_sees_the_workflows() -> None:
    names = {p.name for p in _workflow_files()}
    # Vacuity guard: an empty glob (moved directory, wrong cwd) must not pass silently.
    assert {"ci.yml", "dependency-review.yml"} <= names


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_workflow_has_no_secret_judge_key_or_external_reference(workflow: Path) -> None:
    violations = find_violations(workflow.read_text(encoding="utf-8"))
    assert violations == [], f"{workflow.relative_to(_ROOT)}: {violations}"


# --- detector self-tests: the guard must flag what it claims to ----------------


@pytest.mark.parametrize(
    "snippet",
    [
        "env:\n  TOKEN: ${{ secrets.FOO }}\n",
        "env:\n  TOKEN: ${{ secrets['FOO'] }}\n",
        "env:\n  TOKEN: ${{ secrets .FOO }}\n",
        "run: echo '${{ toJSON(secrets) }}'\n",
        "jobs:\n  call:\n    uses: ./.github/workflows/x.yml\n    secrets: inherit\n",
    ],
)
def test_detector_flags_secret_references(snippet: str) -> None:
    assert any("secrets" in v for v in find_violations(snippet, ())), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        "run: security find-generic-password -s jev-eval-typesafe-key -w\n",
        "run: ./scripts/curation-check\nenv:\n  K: idraa-typesafe-key-ci\n",
        "env:\n  TYPESAFE_API_KEY: x\n",
    ],
)
def test_detector_flags_the_judge_key_literals(snippet: str) -> None:
    assert find_violations(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        "run: uv run clerk run --base abc --external\n",
        "run: clerk run --external --out x\n",
    ],
)
def test_detector_flags_external_clerk_invocations(snippet: str) -> None:
    assert "passes --external" in find_violations(snippet, ())


@pytest.mark.parametrize(
    "snippet",
    [
        # What ci.yml legitimately contains today.
        "  secrets:\n    name: secrets (gitleaks, full history)\n",
        "    needs: [gate, secrets, sast]\n",
        "if: ${{ contains(needs.*.result, 'failure') }}\n",
        "if: needs.secrets.result == 'success'\n",
        "run: gitleaks detect --source . --no-banner --verbose\n",
        "run: uv run clerk run --no-external\n",
        "run: tool --externally-hosted --no-external\n",
    ],
)
def test_detector_leaves_current_legitimate_shapes_alone(snippet: str) -> None:
    assert find_violations(snippet) == [], snippet
