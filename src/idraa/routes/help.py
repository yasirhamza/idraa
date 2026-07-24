"""In-app Help section — article registry routing.

Renders the help index (listing all articles) and individual article pages.
Supports HX-Request content negotiation: HTMX requests get a drawer-shaped
partial; direct navigation gets a full page extending base.html.

Visible to any authenticated role (analyst / reviewer / admin).

Spec: docs/plans/2026-06-13-help-section-design.md
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from idraa.app import templates
from idraa.help_content import (
    HELP_ARTICLES,
    HELP_BY_SLUG,
    HELP_DERIVED,
    HELP_REDIRECTS,
    TRACK_TITLES,
    HelpArticle,
)
from idraa.models.user import User
from idraa.routes._htmx import is_boosted, is_htmx_request
from idraa.routes.deps import require_user
from idraa.services.help_search import search_help

router = APIRouter()


@router.get("/help", response_class=HTMLResponse)
async def help_index(
    request: Request,
    user: User = Depends(require_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "help/index.html",
        {
            "current_user": user,
            "articles": HELP_ARTICLES,
            "track_titles": TRACK_TITLES,
            "help_derived": HELP_DERIVED,
        },
    )


@router.get("/help/search", response_class=HTMLResponse)
async def help_search(
    request: Request,
    q: str = Query("", max_length=256),  # Sec-N3: bound before template context
    user: User = Depends(require_user),
) -> Response:
    # PRArch2-N4: defensive parity with /help/{slug} — a BOOSTED nav ALSO
    # sends HX-Request: true (htmx sets both headers on a boosted request),
    # so checking HX-Request alone would swap this headless partial into
    # the full-page body on a boosted click, destroying page chrome (the
    # Arch-B1 bug /help/{slug} already guards against).
    is_drawer = is_htmx_request(request) and not is_boosted(request)
    if not is_drawer:
        # PRArch-N1: direct-nav (typed/bookmarked URL, no HX-Request) has no
        # sensible bare-partial rendering — a headless fragment with no page
        # chrome is a worse landing than the index. This is now a SECOND
        # response shape alongside the HX-driven partial below, so both vary
        # on HX-Request, HX-Boosted.
        return RedirectResponse(
            "/help", status_code=303, headers={"Vary": "HX-Request, HX-Boosted"}
        )
    return templates.TemplateResponse(
        request,
        "help/_search_results.html",
        {"current_user": user, "q": q, "hits": search_help(q)},
        headers={"Vary": "HX-Request, HX-Boosted"},
    )


@router.get("/help/{slug}", response_class=HTMLResponse)
async def help_article(
    request: Request,
    slug: str,
    user: User = Depends(require_user),
) -> Response:
    is_drawer = is_htmx_request(request) and not is_boosted(request)

    entry: HelpArticle | None
    target = HELP_REDIRECTS.get(slug)
    if target is not None:
        if is_drawer:
            entry = HELP_BY_SLUG[target]  # drawers can't follow 301s — serve content
        else:
            # Boosted + plain navigations both take the 301: htmx uses
            # xhr.responseURL for history, so the URL bar lands on the new slug.
            return RedirectResponse(
                f"/help/{target}",
                status_code=301,
                headers={"Vary": "HX-Request, HX-Boosted"},
            )
    else:
        entry = HELP_BY_SLUG.get(slug)

    if entry is None:
        if is_drawer:
            return templates.TemplateResponse(
                request,
                "help/_not_found.html",
                {"current_user": user},
                status_code=404,
                headers={"Vary": "HX-Request, HX-Boosted"},
            )
        # Sec4-N1: the app-level exception handler propagates exc.headers.
        raise HTTPException(status_code=404, headers={"Vary": "HX-Request, HX-Boosted"})

    related = [HELP_BY_SLUG[s] for s in entry.related]
    derived = HELP_DERIVED[entry.slug]
    siblings = sorted((a for a in HELP_ARTICLES if a.track == entry.track), key=lambda a: a.order)
    idx = siblings.index(entry)
    ctx = {
        "current_user": user,
        "article": entry,
        "related": related,
        "toc": derived.toc,
        "minutes": derived.minutes,
        "track_title": TRACK_TITLES[entry.track],
        "prev_article": siblings[idx - 1] if idx > 0 else None,
        "next_article": siblings[idx + 1] if idx + 1 < len(siblings) else None,
        "articles": HELP_ARTICLES,
        "track_titles": TRACK_TITLES,
        "help_derived": HELP_DERIVED,
        "is_drawer": is_drawer,
    }
    name = "help/_article.html" if is_drawer else "help/article_page.html"
    return templates.TemplateResponse(
        request,
        name,
        ctx,
        headers={"Vary": "HX-Request, HX-Boosted"},
    )
