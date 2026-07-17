"""In-app Help section — article registry (metadata + routing allowlist).

Single source of truth for which Help articles exist, their nav grouping, and
cross-links. Article *bodies* are Jinja templates at
templates/help/articles/<slug>.html. The registry is the allowlist used by
routes/help.py to resolve a slug to a fixed template path (no raw-slug
interpolation -> no path traversal / SSTI).

Design: docs/plans/2026-06-13-help-section-design.md
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpArticle:
    slug: str
    title: str
    cluster: str
    summary: str
    related: tuple[str, ...]


HELP_ARTICLES: tuple[HelpArticle, ...] = (
    HelpArticle(
        slug="getting-started",
        title="Getting started",
        cluster="Getting started",
        summary="What Idraa does, the roles, and the path from scenario to report.",
        related=("build-a-scenario", "methodology-primer"),
    ),
    HelpArticle(
        slug="build-a-scenario",
        title="Build a scenario",
        cluster="Core flow",
        summary="The scenario wizard end to end, with a worked example.",
        related=("methodology-primer", "run-and-read-analyses"),
    ),
    HelpArticle(
        slug="run-and-read-analyses",
        title="Run & read analyses",
        cluster="Core flow",
        summary="Run Monte Carlo and read the loss range, VaR, and expected shortfall.",
        related=("methodology-primer", "reports"),
    ),
    HelpArticle(
        slug="methodology-primer",
        title="FAIR methodology primer",
        cluster="Methodology",
        summary="The FAIR/FAIR-CAM concepts behind the numbers, plus a glossary.",
        related=("build-a-scenario", "run-and-read-analyses"),
    ),
    HelpArticle(
        slug="libraries",
        title="Libraries",
        cluster="Libraries & data",
        summary="Scenario and control libraries, crosswalk, recommendations, and adopt.",
        related=("build-a-scenario", "import-export"),
    ),
    HelpArticle(
        slug="import-export",
        title="Import & export scenarios",
        cluster="Libraries & data",
        summary="Bulk import and export scenarios as CSV or JSON.",
        related=("libraries", "build-a-scenario"),
    ),
    HelpArticle(
        slug="reports",
        title="Reports",
        cluster="Outputs & configuration",
        summary="Generate and read the executive PDF, including control attribution.",
        related=("run-and-read-analyses", "controls-overlays"),
    ),
    HelpArticle(
        slug="controls-overlays",
        title="Controls & overlays",
        cluster="Outputs & configuration",
        summary="Manage the controls and overlays that reduce modeled risk.",
        related=("build-a-scenario", "reports", "control-sub-functions"),
    ),
    HelpArticle(
        slug="control-sub-functions",
        title="FAIR-CAM sub-functions",
        cluster="Outputs & configuration",
        summary="The LEC / VMC / DSC sub-function taxonomy you assign on a control.",
        related=("controls-overlays", "methodology-primer"),
    ),
    HelpArticle(
        slug="control-value-robustness",
        title="Why control value is shown as a range",
        cluster="Outputs & configuration",
        summary=(
            "How to read the control-value ranges, the 'too close to call' flag, "
            "and why the typical-case figure sits below the average."
        ),
        related=("reports", "controls-overlays", "methodology-primer"),
    ),
)

HELP_BY_SLUG: dict[str, HelpArticle] = {a.slug: a for a in HELP_ARTICLES}


def help_url(slug: str) -> str:
    """Return the URL for a help slug, raising KeyError on an unknown slug.

    Registered as a Jinja global so help_trigger(slug) fails a render/test on a
    typo'd slug rather than emitting a silently-404ing button (Arch-N2).
    """
    if slug not in HELP_BY_SLUG:
        raise KeyError(f"Unknown help slug: {slug!r}")
    return f"/help/{slug}"
