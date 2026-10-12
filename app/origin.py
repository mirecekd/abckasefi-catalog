# abckasefi-catalog/app/origin.py
"""IMDb ids of Czech, Slovak and Czechoslovak TV series and films, from TMDB discover; they get titles.origin = 1."""
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db
from . import tmdb_client as client

log = logging.getLogger("catalog.origin")
# TMDB files Czechoslovak productions under XC, so CZ|SK alone misses ~470 series. Films are queried one country
# at a time: CZ|SK|XC together is 682 pages and TMDB refuses pages above LIST_MAX_PAGES (~3600 films would vanish).
QUERIES = (("tv", "CZ|SK|XC", "first_air_date.asc"), ("movie", "CZ", "primary_release_date.asc"),
           ("movie", "SK", "primary_release_date.asc"), ("movie", "XC", "primary_release_date.asc"))
WORKERS = 8
RETRY_S = 2  # pause before the single retry of a failed TMDB call
EXT_TTL = 365 * 86400


class Incomplete(Exception):
    """A TMDB call failed or a result set cannot be read in full: the whole fetch is abandoned."""


def _run(func, items, stop):
    """func over items in a thread pool, in order; the first Incomplete stops the queued rest and propagates."""
    def guarded(item):
        if stop.is_set():
            raise Incomplete("stopped")
        try:
            return func(item)
        except Incomplete:
            stop.set()
            raise
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(guarded, items))


def _call(key, ttl, path, params):
    """client.cached with one retry; a failure (None, never cached) is an error, a cached dict is an answer."""
    for attempt in (0, 1):
        data = client.cached(key, ttl, path, params)
        if data is not None:
            return data
        if attempt == 0:
            time.sleep(RETRY_S)
    raise Incomplete(path)


def _tmdb_ids(media, countries, sort_by, stop):
    """TMDB ids of one discover query, all pages."""
    def page(number):
        return _call(f"origin:{media}:{countries}:{number}", client.LIST_TTL, f"/discover/{media}",
                     {"with_origin_country": countries, "sort_by": sort_by, "page": number})
    first = page(1)
    total = first.get("total_pages") or 1
    if total > client.LIST_MAX_PAGES:
        log.error("origin: %s %s has %d pages, TMDB serves %d", media, countries, total, client.LIST_MAX_PAGES)
        raise Incomplete(f"{media} {countries}: {total} pages")
    pages = [first] + _run(page, range(2, total + 1), stop)
    return {r["id"] for p in pages for r in p.get("results", [])}


def _imdb_id(media_id):
    media, tmdb_id = media_id
    data = _call(f"ext:{media}:{tmdb_id}", EXT_TTL, f"/{media}/{tmdb_id}/external_ids", {})
    return data.get("imdb_id")


def fetch_ids():
    """IMDb ids of every CZ/SK/XC series and film TMDB lists, or None when TMDB is off or anything fails.

    Never a partial set: the importer then keeps the previous flags. Titles TMDB has no IMDb id for are
    skipped, Nokturno plays by IMDb id only. A failed /external_ids call (rate limit, network) must not look
    like "no IMDb id": it is retried once, then the fetch is abandoned; cache.db makes the re-run resume."""
    if not client.enabled():
        return None
    stop = threading.Event()
    try:
        found = [(media, i) for media, countries, sort_by in QUERIES for i in _tmdb_ids(media, countries, sort_by, stop)]
        found = list(dict.fromkeys(found))  # a film can sit under two countries
        ids = {i for i in _run(_imdb_id, found, stop) if i}
    except Incomplete as exc:
        log.error("origin: TMDB fetch abandoned (%s), previous flags stay", exc)
        return None
    log.info("origin: %d TMDB series and films, %d with an IMDb id", len(found), len(ids))
    return ids


def previous_ids():
    """origin=1 ids of the catalog.db about to be replaced (empty when there is none or it predates the column)."""
    with db.catalog() as conn:
        if conn is None:
            return set()
        try:
            return {r[0] for r in conn.execute("SELECT id FROM titles WHERE origin=1")}
        except sqlite3.OperationalError:
            return set()
