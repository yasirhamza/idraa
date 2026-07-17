"""In-app Help section — article registry routing.

Renders the help index (listing all articles) and individual article pages.
Supports HX-Request content negotiation: HTMX requests get a drawer-shaped
partial; direct navigation gets a full page extending base.html.

Visible to any authenticated role (analyst / reviewer / admin).

Spec: docs/plans/2026-06-13-help-section-design.md
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from idraa.app import templates
from idraa.help_content import HELP_ARTICLES, HELP_BY_SLUG
from idraa.models.user import User
from idraa.routes.deps import require_user

router = APIRouter()


@router.get("/help", response_class=HTMLResponse)
async def help_index(
    request: Request,
    user: User = Depends(require_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "help/index.html",
        {"current_user": user, "articles": HELP_ARTICLES},
    )


@router.get("/help/{slug}", response_class=HTMLResponse)
async def help_article(
    request: Request,
    slug: str,
    user: User = Depends(require_user),
) -> HTMLResponse:
    is_hx = request.headers.get("HX-Request") is not None
    entry = HELP_BY_SLUG.get(slug)
    if entry is None:
        if is_hx:
            return templates.TemplateResponse(
                request, "help/_not_found.html", {"current_user": user}, status_code=404
            )
        raise HTTPException(status_code=404)

    related = [HELP_BY_SLUG[s] for s in entry.related]
    name = "help/_article.html" if is_hx else "help/article_page.html"
    return templates.TemplateResponse(
        request,
        name,
        {"current_user": user, "article": entry, "related": related},
    )
