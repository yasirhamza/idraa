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

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


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


def help_url(slug: str, anchor: str | None = None) -> str:
    """URL for a help slug (+ optional section anchor). Raises KeyError on an
    unknown slug OR a dangling anchor, so a typo fails a render/test instead
    of emitting a silently-broken trigger (Arch-N2; spec §Context-sensitive).

    Registered as a Jinja global so help_trigger(slug) fails a render/test on a
    typo'd slug rather than emitting a silently-404ing button.
    """
    if slug not in HELP_BY_SLUG:
        raise KeyError(f"Unknown help slug: {slug!r}")
    if anchor is not None:
        if anchor not in {i for i, _ in HELP_DERIVED[slug].toc}:
            raise KeyError(f"Unknown help anchor for {slug!r}: {anchor!r}")
        return f"/help/{slug}#{anchor}"
    return f"/help/{slug}"


@dataclass(frozen=True)
class DerivedArticle:
    """Never authored — parsed from the article template source at import."""

    minutes: int
    toc: tuple[tuple[str, str], ...]  # (h2 id, heading text)
    search_text: str


_ARTICLES_DIR = Path(__file__).parent / "templates" / "help" / "articles"
_FIGURES_DIR = Path(__file__).parent / "templates" / "help" / "figures"
_JINJA_TAG = re.compile(r"{%.*?%}|{{.*?}}|{#.*?#}", re.DOTALL)
_WORDS_PER_MINUTE = 200
_SCRIPT_TAG = re.compile(r"<\s*script", re.IGNORECASE)


# [\s/] not \s alone (Sec4-N2): <svg/onload=…> slash-separated attributes
# are valid HTML and classic pasted-payload notation.
_EVENT_HANDLER_ATTR = re.compile(r"[\s/]on[a-z]+\s*=", re.IGNORECASE)


def _reject_scripts(source: str, *, name: str, check_event_handlers: bool = False) -> None:
    """Sec-N2/Sec2-N1: raw byte-level check on the UNSTRIPPED source. CSP
    retains 'unsafe-inline' (Alpine), so an inline script in help content
    WOULD execute. The raw check is immune to tokenizer differentials and
    Jinja-stripped `|safe` payloads whose LITERAL SOURCE contains the tag
    bytes (escaped &lt;script prose in an article never matches).

    check_event_handlers (figures only, Sec3-N2b): pasted SVG's more common
    vector is an on*= attribute, not a script tag; article prose could
    false-positive on words like 'online =', so the handler check is scoped
    to figure sources."""
    if _SCRIPT_TAG.search(source):
        raise ValueError(f"{name}: <script> not allowed in help content")
    if check_event_handlers and _EVENT_HANDLER_ATTR.search(source):
        raise ValueError(f"{name}: inline event-handler attribute not allowed in figures")


class _ArticleParser(HTMLParser):
    def __init__(self, slug: str) -> None:
        super().__init__(convert_charrefs=True)
        self.slug = slug
        self.toc: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._h2_id: str | None = None
        self._in_h2 = False
        self._h2_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # (script rejection happens BEFORE parsing, on the raw source —
        # _reject_scripts — so tokenizer differentials can't route around it.)
        if tag == "h2":
            hid = dict(attrs).get("id")
            if not hid:
                raise ValueError(f"{self.slug}: <h2> without id")
            # Sec-N1: anchors ride hrefs, Alpine payloads, and querySelector —
            # keep the charset structurally boring.
            if not re.fullmatch(r"[a-z][a-z0-9-]*", hid):
                raise ValueError(f"{self.slug}: h2 id {hid!r} outside [a-z][a-z0-9-]*")
            if hid in {i for i, _ in self.toc}:
                raise ValueError(f"{self.slug}: duplicate h2 id {hid!r}")
            self._in_h2, self._h2_id, self._h2_text = True, hid, []

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self._in_h2:
            assert self._h2_id is not None  # noqa: S101 -- mypy narrowing, not a runtime guard
            self.toc.append((self._h2_id, " ".join("".join(self._h2_text).split())))
            self._in_h2 = False

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._in_h2:
            self._h2_text.append(data)


def parse_article_source(source: str, *, slug: str) -> DerivedArticle:
    _reject_scripts(source, name=slug)  # BEFORE Jinja-strip — see helper docstring
    p = _ArticleParser(slug)
    p.feed(_JINJA_TAG.sub(" ", source))
    p.close()
    words = len(" ".join(p.text).split())
    return DerivedArticle(
        minutes=max(1, round(words / _WORDS_PER_MINUTE)),
        toc=tuple(p.toc),
        search_text=" ".join(" ".join(p.text).lower().split()),
    )


def _build_derived(
    articles: tuple[HelpArticle, ...] | None = None,  # SC2-I1: explicit Optional (RUF013)
    redirects: dict[str, str] | None = None,
    route_map: tuple[tuple[str, str], ...] | None = None,
    articles_dir: Path | None = None,
    figures_dir: Path | None = None,
) -> dict[str, DerivedArticle]:
    """Module state by default; parameters exist so the failure paths are
    unit-testable without corrupting the real registry (SC-I5)."""
    articles = HELP_ARTICLES if articles is None else articles
    redirects = HELP_REDIRECTS if redirects is None else redirects
    route_map = HELP_ROUTE_MAP if route_map is None else route_map
    articles_dir = _ARTICLES_DIR if articles_dir is None else articles_dir
    figures_dir = _FIGURES_DIR if figures_dir is None else figures_dir
    by_slug = {a.slug: a for a in articles}
    out: dict[str, DerivedArticle] = {}
    # Sec2-I3: figure sources are structurally invisible to the article
    # parser ({% include %} is stripped) — scan them directly. rglob so a
    # P2 subdirectory layout can't silently skip the scan (Sec3-N2a).
    if figures_dir.is_dir():
        for fig in sorted(figures_dir.rglob("*.html")):
            _reject_scripts(
                fig.read_text(encoding="utf-8"), name=fig.name, check_event_handlers=True
            )
    for a in articles:
        f = articles_dir / f"{a.slug}.html"
        if not f.is_file():
            raise ValueError(f"help article body missing: {f.name}")
        out[a.slug] = parse_article_source(f.read_text(encoding="utf-8"), slug=a.slug)
        for r in a.related:
            if r not in by_slug:
                raise ValueError(f"{a.slug}: dangling related slug {r!r}")
    for old, new in redirects.items():
        if new not in by_slug or old in by_slug:
            raise ValueError(f"bad redirect {old!r} -> {new!r}")
    for _, slug in route_map:
        if slug not in by_slug:
            raise ValueError(f"route map -> dangling slug {slug!r}")
    return out


HELP_DERIVED: dict[str, DerivedArticle] = _build_derived()
