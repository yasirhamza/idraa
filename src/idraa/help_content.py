"""In-app Help section — article registry (metadata + routing allowlist).

Single source of truth for which Help articles exist, their track/order, and
cross-links. Article *bodies* are Jinja templates at
templates/help/articles/<slug>.html. The registry is the allowlist used by
routes/help.py to resolve a slug to a fixed template path (no raw-slug
interpolation -> no path traversal / SSTI).

Design: docs/plans/2026-06-13-help-section-design.md,
docs/superpowers/specs/2026-07-24-help-overhaul-design.md (P1 section).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpArticle:
    slug: str
    title: str
    track: str  # "guide" | "methodology"
    order: int  # 1-based position within its track
    summary: str
    related: tuple[str, ...]


TRACK_TITLES: dict[str, str] = {
    "guide": "Using Idraa",
    "methodology": "Methodology & verification",
}


HELP_ARTICLES: tuple[HelpArticle, ...] = (
    HelpArticle(
        "getting-started",
        "Getting started",
        "guide",
        1,
        "What Idraa does, the roles, and the path from scenario to report.",
        ("build-a-scenario", "fair-in-idraa-terms"),
    ),
    HelpArticle(
        "build-a-scenario",
        "Build a scenario",
        "guide",
        2,
        "The scenario wizard end to end, with a worked example.",
        ("fair-in-idraa-terms", "run-and-read-analyses"),
    ),
    HelpArticle(
        "run-and-read-analyses",
        "Run & read analyses",
        "guide",
        3,
        "Run Monte Carlo and read the loss range, VaR, and expected shortfall.",
        ("fair-in-idraa-terms", "reports-and-workbook"),
    ),
    HelpArticle(
        "controls-overlays",
        "Controls & overlays",
        "guide",
        4,
        "Manage the controls and overlays that reduce modeled risk.",
        ("build-a-scenario", "reports-and-workbook", "how-controls-change-the-numbers"),
    ),
    # P1-temporary: merges into libraries-and-data in P2.
    HelpArticle(
        "libraries",
        "Libraries",
        "guide",
        5,
        "Scenario and control libraries, crosswalk, recommendations, and adopt.",
        ("build-a-scenario", "import-export"),
    ),
    # P1-temporary: merges into libraries-and-data in P2.
    HelpArticle(
        "import-export",
        "Import & export scenarios",
        "guide",
        6,
        "Bulk import and export scenarios as CSV or JSON.",
        ("libraries", "build-a-scenario"),
    ),
    HelpArticle(
        "reports-and-workbook",
        "Reports & workbook",
        "guide",
        7,
        "Generate and read the executive PDF, including control attribution, "
        "and the Excel verification workbook.",  # Meth-N2: name what the title names
        ("run-and-read-analyses", "controls-overlays"),
    ),
    HelpArticle(
        "fair-in-idraa-terms",
        "FAIR in Idraa's terms",
        "methodology",
        1,
        "The FAIR/FAIR-CAM concepts behind the numbers, plus a glossary.",
        ("build-a-scenario", "run-and-read-analyses"),
    ),
    # Meth-I2: the P1 body is still the taxonomy card, which explicitly defers
    # the how-it-changes-the-numbers question — the new title would over-promise.
    # Slug renames now (redirects/tests/URLs settle once); the P2 rewrite earns
    # the new title and flips this string.
    HelpArticle(
        "how-controls-change-the-numbers",
        "FAIR-CAM sub-functions",
        "methodology",
        2,
        "The LEC / VMC / DSC sub-function taxonomy you assign on a control.",
        ("controls-overlays", "fair-in-idraa-terms"),
    ),
    HelpArticle(
        "why-values-are-ranges",
        "Why control value is shown as a range",
        "methodology",
        3,
        "How to read the control-value ranges, the 'too close to call' flag, "
        "and why the typical-case figure sits below the average.",
        ("reports-and-workbook", "controls-overlays", "fair-in-idraa-terms"),
    ),
    # P1-temporary: absorbed by verify-the-numbers-yourself in P2.
    HelpArticle(
        "raw-samples-export",
        "Raw sample export",
        "methodology",
        4,
        "Download per-iteration Monte Carlo samples and recompute tail metrics yourself.",
        ("run-and-read-analyses", "reports-and-workbook"),
    ),
)

HELP_BY_SLUG: dict[str, HelpArticle] = {a.slug: a for a in HELP_ARTICLES}


# Old slug -> live slug. Old slugs 301 (full page) / serve the new partial (HX).
HELP_REDIRECTS: dict[str, str] = {
    "methodology-primer": "fair-in-idraa-terms",
    "control-sub-functions": "how-controls-change-the-numbers",
    "control-value-robustness": "why-values-are-ranges",
    "reports": "reports-and-workbook",
}

# Route-prefix -> slug for the route-aware help default. Longest matching
# prefix wins regardless of entry order; entries are kept sorted long->short
# purely for readability. P2 extends this as articles land.
HELP_ROUTE_MAP: tuple[tuple[str, str], ...] = (
    ("/scenarios/import", "import-export"),
    ("/controls/library", "libraries"),
    ("/analyses", "run-and-read-analyses"),
    ("/scenarios", "build-a-scenario"),
    ("/overlays", "controls-overlays"),
    ("/controls", "controls-overlays"),
    ("/library", "libraries"),
    ("/reports", "reports-and-workbook"),
    ("/runs", "run-and-read-analyses"),
)


def help_slug_for_path(path: str) -> str | None:
    """Longest-prefix route->article resolution; None when unmapped.

    ``path`` is ``request.url.path`` — it never carries a query string, so
    segment-boundary matching needs only the exact/`/` cases.
    """
    best: tuple[int, str] | None = None
    for prefix, slug in HELP_ROUTE_MAP:
        matches = path == prefix or path.startswith(prefix + "/")
        if matches and (best is None or len(prefix) > best[0]):
            best = (len(prefix), slug)
    return best[1] if best else None


def help_url(slug: str) -> str:
    """Return the URL for a help slug, raising KeyError on an unknown slug.

    Registered as a Jinja global so help_trigger(slug) fails a render/test on a
    typo'd slug rather than emitting a silently-404ing button (Arch-N2).
    """
    if slug not in HELP_BY_SLUG:
        raise KeyError(f"Unknown help slug: {slug!r}")
    return f"/help/{slug}"
