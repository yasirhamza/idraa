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
        {"current_user": user, "articles": HELP_ARTICLES, "track_titles": TRACK_TITLES},
    )


@router.get("/help/search", response_class=HTMLResponse)
async def help_search(
    request: Request,
    q: str = Query("", max_length=256),  # Sec-N3: bound before template context
    user: User = Depends(require_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "help/_search_results.html",
        {"current_user": user, "q": q, "hits": search_help(q)},
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
    name = "help/_article.html" if is_drawer else "help/article_page.html"
    return templates.TemplateResponse(
        request,
        name,
        {"current_user": user, "article": entry, "related": related},
        headers={"Vary": "HX-Request, HX-Boosted"},
    )
