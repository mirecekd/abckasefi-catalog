# abckasefi-catalog/app/routes_titles.py
"""Title detail, seasons/episodes of a series and the lazy poster redirect."""
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse, RedirectResponse

from . import config, db, tmdb_detail, tmdb_series
from . import tmdb_client as client
from .common import auth, check_lang, check_tt, item_of

router = APIRouter(dependencies=[Depends(auth)])


@router.get("/v1/title/{title_id}")
def title(title_id: str, lang: str = ""):
    """Detail: catalog row (or, for titles the catalog does not hold - new or rarely voted ones that TMDB
    lists show - the TMDB data) + lazily fetched Czech overview/poster (cached) + Nokturno play links."""
    check_tt(title_id)
    check_lang(lang)
    if not db.ready():  # empty/rebuilding catalog is a temporary 503, not a "this title does not exist" 404
        raise HTTPException(503, "catalog is still being built, retry in a few minutes")
    row = db.get(title_id)
    extra = tmdb_detail.detail(title_id, row["type"] if row else "", lang) or {}
    if row:
        item = item_of(row, lang)
        kind = row["type"]
    elif extra:
        kind = "series" if extra.get("media") == "tv" else "movie"
        year = extra.get("year") or ""
        item = {"id": title_id, "type": kind, "title": extra.get("title") or title_id, "title_lang": config.TMDB_LANG[:2],
                "titles": {}, "original": extra.get("original"),
                "year": int(year) if year.isdigit() else None, "rating": None, "votes": None,
                "genres": extra.get("genres") or [], "czsk": False, "poster": f"/v1/poster/{title_id}"}
    else:
        raise HTTPException(404, "unknown id")
    item["overview"] = extra.get("overview", "")
    item["overview_lang"] = extra.get("overview_lang", "")
    item["tmdb_rating"] = extra.get("tmdb_rating")
    item["runtime"] = extra.get("runtime")
    item["poster_url"] = client.image_url(extra.get("poster"), "w500")
    item["backdrop_url"] = client.image_url(extra.get("backdrop"), "w780")
    base = "plugin://plugin.video.nokturno/"
    item["play"] = (f"{base}?action=play&type=movie&id={title_id}&ask=1" if kind == "movie"
                    else f"{base}?action=seasons&id={title_id}")
    return item


def _episode_play(title_id, season, episode):
    """Nokturno plays an episode by id `tt:S:E` and needs the series id too (see its TMDb Helper player)."""
    return (f"plugin://plugin.video.nokturno/?action=play&type=series&id={title_id}%3A{season}%3A{episode}"
            f"&series={title_id}&ask=1")


@router.get("/v1/title/{title_id}/seasons")
def title_seasons(title_id: str):
    """Seasons of a series (from TMDB, cached)."""
    check_tt(title_id)
    if not client.enabled():
        raise HTTPException(400, "TMDB_API_KEY not set")
    found = tmdb_series.seasons(title_id)
    play = f"plugin://plugin.video.nokturno/?action=seasons&id={title_id}"
    if found is None:
        row = db.get(title_id)
        if not row or row["type"] != "series":
            raise HTTPException(404, "not a known series")
        # a real series, but neither TMDB nor Cinemeta lists episodes: say so instead of failing
        return {"id": title_id, "play": play, "seasons": [],
                "note": "Epizody tohoto seriálu nejsou v TMDB ani v Cinemetě. Nokturno je může mít z vlastních zdrojů."}
    return {"id": title_id, "play": play,
            "seasons": [{**s, "poster_url": client.image_url(s["poster"], "w185")} for s in found]}


@router.get("/v1/title/{title_id}/season/{season}")
def title_season(title_id: str, season: int = Path(ge=0, le=200), lang: str = ""):
    """Episodes of one season with a Nokturno play link for each."""
    check_tt(title_id)
    check_lang(lang)
    if not client.enabled():
        raise HTTPException(400, "TMDB_API_KEY not set")
    eps = tmdb_series.episodes(title_id, season, lang)
    if eps is None:
        raise HTTPException(404, "no such season")
    return {"id": title_id, "season": season, "episodes": [
        {**e, "still_url": e.get("still_url") or client.image_url(e["still"], "w300"),
         "play": _episode_play(title_id, season, e["episode"])} for e in eps]}


@router.get("/v1/poster/{title_id}")
def poster(title_id: str, size: str = "w185"):
    """Redirect to the TMDB poster (resolved on first request, then cached); 404 if none.
    Image clients cannot send headers, so with API_TOKEN set they pass `?token=` (it protects the TMDB quota)."""
    check_tt(title_id)
    if size not in ("w92", "w154", "w185", "w342", "w500"):
        raise HTTPException(400, "bad size")
    extra = tmdb_detail.detail(title_id, "")
    url = client.image_url((extra or {}).get("poster"), size)
    if not url:
        return JSONResponse({"detail": "no poster"}, status_code=404)
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "public, max-age=86400"})
