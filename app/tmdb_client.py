# abckasefi-catalog/app/tmdb_client.py
"""Thin TMDB v3 client. Everything is fetched on demand and cached in cache.db; no key = no calls.

TMDB_API_KEY may be the v3 api key (32 hex chars) or the v4 "read access token" (a long JWT).
"""
import logging

import httpx

from . import config, db

log = logging.getLogger("catalog.tmdb")
API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
TMDB_LOCALE = {"cs": "cs-CZ", "sk": "sk-SK", "en": "en-US"}
LIST_TTL = 6 * 3600
LIST_PAGE_SIZE = 20  # TMDB serves every list in pages of 20
LIST_MAX_PAGES = 500  # TMDB answers HTTP 400 beyond page 500 although total_pages can say 1001
http = httpx.Client(timeout=15)

# Lists TMDB offers (verified against developer.themoviedb.org/openapi, 2026-10): key -> (label, path, params)
LISTS = {
    "movie": {
        "top_rated": ("Nejlépe hodnocené", "/movie/top_rated", {}),
        "popular": ("Populární", "/movie/popular", {}),
        "now_playing": ("Právě v kinech", "/movie/now_playing", {}),
        "upcoming": ("Připravované", "/movie/upcoming", {}),
        "trending_day": ("Trend dnes", "/trending/movie/day", {}),
        "trending_week": ("Trend týdne", "/trending/movie/week", {}),
    },
    "series": {
        "top_rated": ("Nejlépe hodnocené", "/tv/top_rated", {}),
        "popular": ("Populární", "/tv/popular", {}),
        "airing_today": ("Dnes ve vysílání", "/tv/airing_today", {}),
        "on_the_air": ("Právě vysílané", "/tv/on_the_air", {}),
        "trending_day": ("Trend dnes", "/trending/tv/day", {}),
        "trending_week": ("Trend týdne", "/trending/tv/week", {}),
    },
}


def enabled():
    return bool(config.TMDB_KEY)


def request(path, params):
    """GET one TMDB endpoint; returns parsed JSON or None on any failure (logged, never raised)."""
    headers, query = {}, {"language": config.TMDB_LANG, **params}
    if config.TMDB_KEY.startswith("eyJ"):  # v4 read token
        headers["Authorization"] = "Bearer " + config.TMDB_KEY
    else:
        query["api_key"] = config.TMDB_KEY
    try:
        resp = http.get(API + path, params=query, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("TMDB %s failed: %s", path, type(exc).__name__)
        return None


def cached(key, ttl, path, params):
    if not enabled():
        return None
    hit = db.kv_get(key, ttl)
    if hit is not None:
        return hit
    data = request(path, params)
    if data is not None:
        db.kv_set(key, data)
    return data


def find(imdb_id):
    """IMDb id -> {'tmdb_id', 'media'} using /find; cached for a long time."""
    data = cached("find:" + imdb_id, 365 * 86400, "/find/" + imdb_id, {"external_source": "imdb_id"})
    if not data:
        return None
    for media, key in (("movie", "movie_results"), ("tv", "tv_results")):
        if data.get(key):
            return {"tmdb_id": data[key][0]["id"], "media": media}
    return None


def image_url(path, size="w342"):
    return IMG + size + path if path else None


def tmdb_list(kind, name, page):
    """One page of a TMDB list (top_rated, popular, ...); results carry a TMDB id, mapped to IMDb lazily."""
    spec = LISTS.get(kind, {}).get(name)
    if not spec:
        return None
    return cached(f"list:{kind}:{name}:{page}", LIST_TTL, spec[1], {"page": page})


def imdb_of(media, tmdb_id):
    """TMDB id -> IMDb id via /external_ids (cached long)."""
    data = cached(f"ext:{media}:{tmdb_id}", 365 * 86400, f"/{media}/{tmdb_id}/external_ids", {})
    return (data or {}).get("imdb_id")
