# abckasefi-catalog/app/tmdb_series.py
"""Seasons and episodes of a series: TMDB first, Cinemeta (the source Stremio uses) as the fallback."""
import logging

import httpx

from . import config, db
from . import tmdb_client as client

log = logging.getLogger("catalog.tmdb")
CINEMETA = "https://v3-cinemeta.strem.io/meta/series/"  # fallback for series TMDB does not know
AIRING_TTL = 6 * 3600  # seasons/episodes change while a series is airing


def _tv_id(imdb_id):
    found = client.find(imdb_id)
    return found["tmdb_id"] if found and found["media"] == "tv" else None


def _cinemeta(imdb_id):
    """Cinemeta series meta (videos = episodes) or None; cached, errors never raised."""
    key = "cinemeta:" + imdb_id
    hit = db.kv_get(key, AIRING_TTL)
    if hit is not None:
        return hit or None
    try:
        # Cinemeta answers 307 to cinemeta-live.strem.io for some ids; only this call follows redirects
        resp = client.http.get(CINEMETA + imdb_id + ".json", headers={"User-Agent": "abckasefi-catalog/0.1"},
                               follow_redirects=True)
        if resp.status_code == 404:  # Cinemeta answers 404 for series it does not know: a real "nothing", cache it
            db.kv_set(key, {})
            return None
        resp.raise_for_status()
        meta = resp.json().get("meta") or {}
    except (httpx.HTTPError, ValueError) as exc:  # network trouble: do not cache, try again next time
        log.warning("Cinemeta %s failed: %s", imdb_id, type(exc).__name__)
        return None
    videos = [v for v in meta.get("videos", []) if str(v.get("season", "")).lstrip("-").isdigit()
              and str(v.get("episode") or v.get("number") or "").isdigit()]
    db.kv_set(key, {"videos": videos} if videos else {})  # an empty dict caches "Cinemeta has nothing"
    return {"videos": videos} if videos else None


def seasons(imdb_id):
    """Seasons of a series (specials = season 0 last), or None when neither TMDB nor Cinemeta lists any."""
    tv = _tv_id(imdb_id)
    data = client.cached(f"tv:{tv}", AIRING_TTL, f"/tv/{tv}", {}) if tv else None
    if data:
        out = [{"season": s.get("season_number"), "name": s.get("name") or "", "episodes": s.get("episode_count") or 0,
                "air_date": s.get("air_date") or "", "poster": s.get("poster_path"), "source": "tmdb"}
               for s in data.get("seasons", []) if s.get("season_number") is not None]
        return sorted(out, key=lambda s: (s["season"] == 0, s["season"]))
    cm = _cinemeta(imdb_id)
    if not cm:
        return None
    counts = {}
    for v in cm["videos"]:
        counts[int(v["season"])] = counts.get(int(v["season"]), 0) + 1
    return sorted(({"season": n, "name": "", "episodes": c, "air_date": "", "poster": None, "source": "cinemeta"}
                   for n, c in counts.items()), key=lambda s: (s["season"] == 0, s["season"]))


def _cinemeta_episodes(imdb_id, season):
    cm = _cinemeta(imdb_id)
    if not cm:
        return None
    out = []
    for v in cm["videos"]:
        if int(v["season"]) != season:
            continue
        text = v.get("overview") or v.get("description") or ""
        out.append({"episode": int(v.get("episode") or v.get("number")), "name": v.get("name") or v.get("title") or "",
                    "overview": text, "overview_lang": "en" if text else "", "air_date": (v.get("released") or "")[:10],
                    "runtime": None, "still": None, "still_url": v.get("thumbnail"),
                    "rating": float(v["rating"]) if str(v.get("rating") or "").replace(".", "", 1).isdigit() else None})
    return sorted(out, key=lambda e: e["episode"]) or None


def episodes(imdb_id, season, lang=""):
    """Episodes of one season in `lang` (cs|sk|en; empty = default), English where that language has no text."""
    tv = _tv_id(imdb_id)
    path = f"/tv/{tv}/season/{season}"
    code = lang or config.TMDB_LANG[:2]
    locale = client.TMDB_LOCALE.get(code, config.TMDB_LANG)
    data = client.cached(f"season:{tv}:{season}:{code}", AIRING_TTL, path, {"language": locale}) if tv else None
    if not data:
        return _cinemeta_episodes(imdb_id, season)
    eps = data.get("episodes", [])
    english = {}
    if code != "en" and any(not e.get("overview") for e in eps):  # one extra call per season, only when needed
        en = client.cached(f"season-en:{tv}:{season}", AIRING_TTL, path, {"language": "en-US"}) or {}
        english = {e.get("episode_number"): e for e in en.get("episodes", [])}
    out = []
    for e in eps:
        num = e.get("episode_number")
        if num is None:
            continue
        text, used = e.get("overview") or "", code
        name = e.get("name") or ""
        alt = english.get(num) or {}
        if not text and alt.get("overview"):
            text, used = alt["overview"], "en"
        if not name or name.lower().startswith(("epizoda ", "episode ", "epizóda ")):  # TMDB's placeholder title
            name = alt.get("name") or name
        out.append({"episode": num, "name": name, "overview": text, "overview_lang": used if text else "",
                    "air_date": e.get("air_date") or "", "runtime": e.get("runtime"), "still": e.get("still_path"),
                    "rating": e.get("vote_average")})
    return out
