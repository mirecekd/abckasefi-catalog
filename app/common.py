# abckasefi-catalog/app/common.py
"""Shared pieces of the HTTP layer: auth, input validation and the public JSON shape of a title."""
import re
import secrets

from fastapi import Header, HTTPException, Query

from . import config, importer

VERSION = "0.1.0"
KINDS = ("movie", "series")
PAGE_MAX = 200
LANGS = ("cs", "sk", "en")
MIN_RATED = 25  # a rating from fewer votes than this is shown but never ranked above better-supported titles
TT_RE = re.compile(r"^tt\d{1,10}$")


def auth(authorization: str = Header(default=""), token: str = Query(default="")):
    """Optional shared token (API_TOKEN): `Authorization: Bearer <t>` or `?token=<t>`. Unset = open."""
    if not config.API_TOKEN:
        return
    given = authorization.removeprefix("Bearer ").strip() or token
    if not secrets.compare_digest(given, config.API_TOKEN):
        raise HTTPException(401, "bad token")


def fts_query(text):
    """Turn free text into a safe FTS5 prefix query: every word quoted, last word as prefix."""
    words = re.findall(r"\w+", importer.sortkey(text))[:6]
    if not words:
        return ""
    parts = [f'"{w}"' for w in words[:-1]] + [f'"{words[-1]}"*']
    return " ".join(parts)


def need_ready(result):
    if result is None:
        raise HTTPException(503, "catalog is still being built, retry in a few minutes")
    return result


def check_kind(kind):
    if kind not in KINDS:
        raise HTTPException(400, "type must be movie or series")
    return kind


def check_tt(title_id):
    """IMDb ids end up in TMDB request paths, so anything but ttNNN is treated as unknown."""
    if not TT_RE.match(title_id):
        raise HTTPException(404, "unknown id")
    return title_id


def check_lang(lang):
    """Validated display language (cs/sk/en); empty = the default (Czech, falling back like the catalog does)."""
    if lang and lang not in LANGS:
        raise HTTPException(400, "lang must be one of " + ", ".join(LANGS))
    return lang


def _name(row, lang):
    """Title in the requested language; when the title has none, fall back cs -> sk -> en/original so a card
    is never blank. Returns (title, lang_actually_used)."""
    chain = {"cs": ("cs", "sk"), "sk": ("sk", "cs"), "en": ("en",)}.get(lang or "cs", ("cs", "sk"))
    for code in chain:
        if row.get("name_" + code):
            return row["name_" + code], code
    return row["name"], ("cs" if row["local"] else "en")


def item_of(row, lang=""):
    """Row from titles -> public JSON; poster is a lazy URL (fetched from TMDB only when requested)."""
    title, used = _name(row, lang)
    return {
        "id": row["id"], "type": row["type"], "title": title, "title_lang": used,
        "titles": {c: row.get("name_" + c) for c in LANGS if row.get("name_" + c)},
        "original": row["orig"], "year": row["year"],
        "rating": row["rating"], "votes": row["votes"], "genres": (row["genres"] or "").split(",") if row["genres"] else [],
        "czsk": bool(row["local"]), "poster": f"/v1/poster/{row['id']}",
        "rating_reliable": (row["votes"] or 0) >= MIN_RATED,
    }
