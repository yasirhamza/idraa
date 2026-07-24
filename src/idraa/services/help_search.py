"""In-memory help search over the registry's derived index (spec §Search).

Weighted AND-of-terms with prefix support: title x4, summary x2, heading x2,
body x1. Fifteen-article scale — no stemming, no persistence, no deps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from idraa.help_content import HELP_ARTICLES, HELP_DERIVED

_TOKEN = re.compile(r"[a-z0-9]+")
MAX_QUERY_LEN = 64


@dataclass(frozen=True)
class HelpHit:
    slug: str
    title: str
    summary: str
    heading: str  # first heading matching any term, else ""
    score: int


@dataclass(frozen=True)
class _Doc:
    slug: str
    title: str
    summary: str
    title_tokens: tuple[str, ...]
    summary_tokens: tuple[str, ...]
    headings: tuple[tuple[str, tuple[str, ...]], ...]  # (heading text, tokens)
    body_tokens: frozenset[str]


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(text.lower()))


_INDEX: tuple[_Doc, ...] = tuple(
    _Doc(
        slug=a.slug,
        title=a.title,
        summary=a.summary,
        title_tokens=_tokens(a.title),
        summary_tokens=_tokens(a.summary),
        headings=tuple((h, _tokens(h)) for _, h in HELP_DERIVED[a.slug].toc),
        body_tokens=frozenset(_tokens(HELP_DERIVED[a.slug].search_text)),
    )
    for a in HELP_ARTICLES
)


def _term_score(doc: _Doc, term: str) -> tuple[int, str]:
    """(weight, matched heading text) for one term; weight 0 == no match."""
    if any(t.startswith(term) for t in doc.title_tokens):
        return 4, ""
    for heading, toks in doc.headings:
        if any(t.startswith(term) for t in toks):
            return 2, heading
    if any(t.startswith(term) for t in doc.summary_tokens):
        return 2, ""
    if term in doc.body_tokens or any(t.startswith(term) for t in doc.body_tokens):
        return 1, ""
    return 0, ""


def search_help(q: str, *, limit: int = 8) -> list[HelpHit]:
    terms = _tokens(q[:MAX_QUERY_LEN])
    if not terms or all(len(t) < 2 for t in terms):
        return []
    hits: list[HelpHit] = []
    for doc in _INDEX:
        total, heading = 0, ""
        for term in terms:
            w, h = _term_score(doc, term)
            if w == 0:
                total = 0
                break
            total += w
            heading = heading or h
        if total:
            hits.append(HelpHit(doc.slug, doc.title, doc.summary, heading, total))
    hits.sort(key=lambda h: (-h.score, h.title))
    return hits[:limit]
