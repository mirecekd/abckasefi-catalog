# abckasefi-catalog/app/routes_catalog.py
"""Catalog browsing, A-Z prefix drill-down and full-text search routes."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from . import db, overviews, prefixes
from .common import (
    KINDS,
    PAGE_MAX,
    auth,
    check_kind,
    check_lang,
    check_prefix,
    fts_query,
    item_of,
    need_ready,
)

SEARCH_SORTS = ("popular", "votes", "name", "rating", "year")
router = APIRouter(dependencies=[Depends(auth)])


@router.get("/v1/letters")
def letters(type: str = "movie"):
    return need_ready(db.letters(check_kind(type)))


@router.get("/v1/prefixes")
def prefix_children(type: str = "movie", prefix: str = ""):
    """One level of the A-Z drill-down: how many titles start with `prefix` (total), how many equal it (exact) and
    how many start with each longer prefix (children). Counted over the normalised key (a-z, 0-9 only)."""
    return need_ready(prefixes.children(check_kind(type), check_prefix(prefix)))


@router.get("/v1/titles")
def titles(background: BackgroundTasks, type: str = "movie", letter: str = "A", sort: str = "name", lang: str = "",
           prefix: str = "", exact: int = Query(0, ge=0, le=1),
           page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=PAGE_MAX)):
    """Titles of one letter, or - with `prefix` (1-10 of a-z, 0-9; `letter` is then ignored) - of one prefix;
    `exact=1` keeps only titles whose normalised key equals the prefix (with an empty prefix: titles whose key is
    empty, e.g. purely non-Latin names). `total` counts all matches."""
    check_lang(lang)
    if sort not in db.SORTS:
        raise HTTPException(400, "sort must be one of " + ", ".join(db.SORTS))
    kind, offset = check_kind(type), (page - 1) * per_page
    if prefix or exact:
        checked = check_prefix(prefix, 0 if exact else 1)
        rows, total = need_ready(prefixes.titles(kind, checked, bool(exact), sort, per_page + 1, offset))
    else:
        letter = letter.upper()[:3]
        rows = need_ready(db.browse(kind, letter, sort, per_page + 1, offset))
        total = need_ready(db.letter_total(kind, letter))
    items = [item_of(r, lang) for r in rows[:per_page]]
    overviews.attach(items, background, lang)
    return {"page": page, "per_page": per_page, "has_more": len(rows) > per_page, "total": total, "items": items}


@router.get("/v1/search")
def search(background: BackgroundTasks, q: str = Query(min_length=2, max_length=80), type: str = "",
           sort: str = "popular", lang: str = "", limit: int = Query(40, ge=1, le=PAGE_MAX)):
    """Full-text search. sort = popular (default, by votes; alias votes) | name | rating | year; the order is applied to
    all matches before `limit`, so 'rating' returns the best-rated N hits, not a re-sorted page.
    `has_more` says whether more than `limit` titles match."""
    if type and type not in KINDS:
        raise HTTPException(400, "type must be movie, series or empty")
    if sort not in SEARCH_SORTS:
        raise HTTPException(400, "sort must be one of " + ", ".join(SEARCH_SORTS))
    check_lang(lang)
    match = fts_query(q)
    rows = need_ready(db.search(match, type, sort, limit + 1)) if match else []
    items = [item_of(r, lang) for r in rows[:limit]]
    overviews.attach(items, background, lang)
    return {"has_more": len(rows) > limit, "items": items}
