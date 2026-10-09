# abckasefi-catalog/app/routes_lists.py
"""TMDB list routes: lists mapped to IMDb ids, paged by `per_page` on top of TMDB's pages of 20."""
import math
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from . import config, db, importer, overviews
from . import tmdb_client as client
from .common import MIN_RATED, auth, check_kind, check_lang, item_of

LIST_SORTS = ("tmdb", "rating", "votes", "year", "name")
IMDB_WORKERS = 8
router = APIRouter(dependencies=[Depends(auth)])


@router.get("/v1/lists")
def lists():
    """Names of the TMDB lists (Top rated, Popular, ...) the service can serve."""
    return {k: {n: v[0] for n, v in d.items()} for k, d in client.LISTS.items()}


def _list_key(sort):
    """Sort key for one page of a TMDB list. 'rating' ranks well-supported ratings first (see MIN_RATED)."""
    if sort == "rating":
        return lambda i: (-((i["votes"] or 0) >= MIN_RATED), -(i["rating"] or 0), -(i["votes"] or 0))
    if sort == "votes":
        return lambda i: -(i["votes"] or 0)
    if sort == "year":
        return lambda i: -(i["year"] or 0)
    return lambda i: importer.sortkey(i["title"] or "")


def _tmdb_item(result, imdb, kind):
    """Public JSON for a list entry the catalog does not hold, built from the TMDB result itself."""
    return {
        "id": imdb, "type": kind, "title": result.get("title") or result.get("name"), "title_lang": config.TMDB_LANG[:2],
        "titles": {}, "original": None,
        "year": int((result.get("release_date") or result.get("first_air_date") or "0")[:4] or 0) or None,
        "rating": result.get("vote_average"), "votes": result.get("vote_count"), "genres": [], "czsk": False,
        "poster": f"/v1/poster/{imdb}", "overview": "", "overview_lang": ""}


def _window(kind, name, page, per_page):
    """(results, total_items, total_pages) for the global slice [offset, offset+per_page) of a TMDB list.

    Only the TMDB pages overlapping the slice are fetched (each is cached). Returns None when TMDB fails.
    TMDB refuses pages above LIST_MAX_PAGES, so the list is clamped to 500 x 20 items and a page past that
    end is an empty result, never an upstream error."""
    offset = (page - 1) * per_page
    first = offset // client.LIST_PAGE_SIZE + 1
    last = (offset + per_page - 1) // client.LIST_PAGE_SIZE + 1
    head = client.tmdb_list(kind, name, min(first, client.LIST_MAX_PAGES))
    if head is None:
        return None
    tmdb_pages = head.get("total_pages")
    reachable_pages = min(tmdb_pages or 0, client.LIST_MAX_PAGES)
    reachable = reachable_pages * client.LIST_PAGE_SIZE
    total = min(head.get("total_results") or reachable, reachable)
    if offset >= total:
        return [], total, tmdb_pages
    results = list(head.get("results", []))
    for number in range(first + 1, min(last, reachable_pages) + 1):
        more = client.tmdb_list(kind, name, number)
        if more is None:
            return None
        results += more.get("results", [])
    start = offset - (first - 1) * client.LIST_PAGE_SIZE
    return results[start:start + per_page], total, tmdb_pages


@router.get("/v1/list/{kind}/{name}")
def tmdb_list(background: BackgroundTasks, kind: str, name: str, page: int = Query(1, ge=1, le=500), sort: str = "tmdb",
              lang: str = "", per_page: int = Query(20, ge=1, le=100)):
    """A TMDB list mapped to IMDb ids; titles are returned even if the catalog does not hold them.

    sort = tmdb (TMDB's own order: trend, popularity...) | rating | votes | year | name. The order applies within
    the requested page only (and says so in `sorted_within_page`). TMDB serves pages of 20; `per_page` (1..100)
    re-slices them, `total_pages` is recomputed for it and `has_more` says whether a later page exists."""
    check_kind(kind)
    check_lang(lang)
    if sort not in LIST_SORTS:
        raise HTTPException(400, "sort must be one of " + ", ".join(LIST_SORTS))
    window = _window(kind, name, page, per_page)
    if window is None:
        raise HTTPException(503 if client.enabled() else 400, "TMDB unavailable or TMDB_API_KEY not set")
    results, total, tmdb_pages = window
    media = "tv" if kind == "series" else "movie"
    with ThreadPoolExecutor(max_workers=IMDB_WORKERS) as pool:
        imdb_ids = list(pool.map(lambda r: client.imdb_of(media, r["id"]), results))
    out = []
    from_tmdb = {}  # imdb id -> overview text that came with the list result itself
    for r, imdb in zip(results, imdb_ids, strict=True):
        if not imdb:
            continue
        from_tmdb[imdb] = r.get("overview") or ""
        row = db.get(imdb)
        out.append(item_of(row, lang) if row else _tmdb_item(r, imdb, kind))
    overviews.attach(out, background, lang, from_tmdb)
    if sort != "tmdb":
        out.sort(key=_list_key(sort))
    for i in out:  # a few votes say nothing about quality: flag it so UIs do not present 9.0/1 vote as a hit
        i["rating_reliable"] = (i["votes"] or 0) >= MIN_RATED
    return {"page": page, "per_page": per_page, "has_more": page * per_page < total,
            "total_pages": math.ceil(total / per_page) if tmdb_pages is not None else None,
            "sort": sort, "sorted_within_page": sort != "tmdb", "items": out}
