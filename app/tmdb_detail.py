# abckasefi-catalog/app/tmdb_detail.py
"""Per-title TMDB detail (overview, poster, rating...) cached per title and per language."""
from . import config, db
from . import tmdb_client as client

DETAIL_TTL = 30 * 86400
DETAIL_VERSION = 4  # v2: media/year/genres/original; v3: overview_lang + EN fallback; v4: overview cached per language


def _overview(media, tmdb_id, lang):
    """(overview, tagline, language actually used). Tries the requested language, then English; a tagline
    belongs to the same fetch. Two calls at most, and `lang` decides which one is tried first."""
    wanted = lang or config.TMDB_LANG[:2]
    order = [wanted] + (["en"] if wanted != "en" else [])
    for code in order:
        data = client.request(f"/{media}/{tmdb_id}", {"language": client.TMDB_LOCALE.get(code, config.TMDB_LANG)})
        if data and data.get("overview"):
            return data["overview"], data.get("tagline") or "", code
    return "", "", ""


def detail(imdb_id, kind, lang=""):
    """Overview in `lang` (cs|sk|en; empty = default), poster, backdrop, TMDB rating, runtime (or None).

    Poster/rating/year/genres come from one fetch cached under `detail`; the overview text is cached per
    language, so switching language never re-downloads the rest and never serves the wrong language.
    """
    key = f"detail{DETAIL_VERSION}:{imdb_id}"  # bump DETAIL_VERSION when the stored fields change
    base = db.kv_get(key, DETAIL_TTL) if client.enabled() else None
    if base is None:
        found = client.find(imdb_id)
        if not found:
            return None
        data = client.request(f"/{found['media']}/{found['tmdb_id']}", {})
        if data is None:
            return None
        base = {
            "media": found["media"], "year": (data.get("release_date") or data.get("first_air_date") or "")[:4],
            "genres": [g["name"] for g in data.get("genres", []) if g.get("name")],
            "original": data.get("original_title") or data.get("original_name"),
            "tmdb_id": found["tmdb_id"], "poster": data.get("poster_path"), "backdrop": data.get("backdrop_path"),
            "tmdb_rating": data.get("vote_average"), "tmdb_votes": data.get("vote_count"),
            "runtime": data.get("runtime") or (data.get("episode_run_time") or [None])[0],
            "title": data.get("title") or data.get("name"), "seasons": data.get("number_of_seasons"),
        }
        db.kv_set(key, base)
    code = lang or config.TMDB_LANG[:2]
    okey = f"overview{DETAIL_VERSION}:{code}:{imdb_id}"
    text = db.kv_get(okey, DETAIL_TTL)
    if text is None:
        overview, tagline, used = _overview(base["media"], base["tmdb_id"], lang)
        text = {"overview": overview, "tagline": tagline, "overview_lang": used}
        db.kv_set(okey, text)
    return {**base, **text}
