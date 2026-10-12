# abckasefi-catalog/app/origin.py
"""IMDb ids of Czech, Slovak and Czechoslovak TV series, from TMDB discover; they get titles.origin = 1."""
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from . import db
from . import tmdb_client as client

log = logging.getLogger("catalog.origin")
COUNTRIES = "CZ|SK|XC"  # TMDB files Czechoslovak productions under XC, so CZ|SK alone misses ~470 series
WORKERS = 8


def _page(number):
    return client.cached(f"origin:tv:{number}", client.LIST_TTL, "/discover/tv",
                         {"with_origin_country": COUNTRIES, "sort_by": "first_air_date.asc", "page": number})


def fetch_ids():
    """IMDb ids of every CZ/SK/XC series TMDB lists, or None when TMDB is off or any page fails (never a partial set).

    Series TMDB has no IMDb id for are skipped: Nokturno plays by IMDb id only."""
    if not client.enabled():
        return None
    first = _page(1)
    if first is None:
        return None
    pages = min(first.get("total_pages") or 1, client.LIST_MAX_PAGES)
    results = list(first.get("results", []))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for page in pool.map(_page, range(2, pages + 1)):
            if page is None:
                return None
            results += page.get("results", [])
        ids = {i for i in pool.map(lambda r: client.imdb_of("tv", r["id"]), results) if i}
    log.info("origin: %d TMDB series, %d with an IMDb id", len(results), len(ids))
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
