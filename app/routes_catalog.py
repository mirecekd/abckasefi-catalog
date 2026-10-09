# abckasefi-catalog/app/routes_catalog.py
"""Catalog browsing and full-text search routes."""
from fastapi import APIRouter, Depends, HTTPException, Query

from . import db
from .common import KINDS, PAGE_MAX, auth, check_kind, check_lang, fts_query, item_of, need_ready

SEARCH_SORTS = ("popular", "votes", "name", "rating", "year")
router = APIRouter(dependencies=[Depends(auth)])


@router.get("/v1/letters")
def letters(type: str = "movie"):
    return need_ready(db.letters(check_kind(type)))


@router.get("/v1/titles")
def titles(type: str = "movie", letter: str = "A", sort: str = "name", lang: str = "",
           page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=PAGE_MAX)):
    check_lang(lang)
    if sort not in db.SORTS:
        raise HTTPException(400, "sort must be one of " + ", ".join(db.SORTS))
    # one extra row tells whether another page follows
    rows = need_ready(db.browse(check_kind(type), letter.upper()[:3], sort, per_page + 1, (page - 1) * per_page))
    return {"page": page, "per_page": per_page, "has_more": len(rows) > per_page,
            "items": [item_of(r, lang) for r in rows[:per_page]]}


@router.get("/v1/search")
def search(q: str = Query(min_length=2, max_length=80), type: str = "", sort: str = "popular", lang: str = "",
           limit: int = Query(40, ge=1, le=PAGE_MAX)):
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
    return {"has_more": len(rows) > limit, "items": [item_of(r, lang) for r in rows[:limit]]}
