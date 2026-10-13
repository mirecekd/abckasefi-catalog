# abckasefi-catalog/app/importer.py
"""Build catalog.db from the public IMDb datasets (https://datasets.imdbws.com/, personal non-commercial use).

The new database is built next to the live one and swapped in with os.replace(), so readers never see
a half-built file. Run via `python -m app.importer` or from the scheduler in main.py.
"""
import gzip
import logging
import os
import pathlib
import sqlite3
import time

import httpx

from . import config, db, origin
from .keys import skey, sortkey

log = logging.getLogger("catalog.import")
BASE = "https://datasets.imdbws.com/"
FILES = ("title.basics", "title.ratings", "title.akas")
KINDS = {"movie": "movie", "tvSeries": "series", "tvMiniSeries": "series"}
FRESH_S = 20 * 3600  # a downloaded dataset younger than this is reused


def letter_of(key):
    first = key.lstrip(" \"'.-([¿¡")[:1].upper()
    return first if first.isascii() and first.isalpha() else "0-9"


def download(name, workdir):
    """Download one dataset unless a fresh copy is already there. Raises httpx.HTTPError / OSError."""
    path = os.path.join(workdir, name + ".tsv.gz")
    if os.path.exists(path) and os.path.getmtime(path) > time.time() - FRESH_S:
        return path
    log.info("downloading %s", name)
    tmp = path + ".part"
    try:
        with httpx.stream("GET", BASE + name + ".tsv.gz", timeout=120, follow_redirects=True,
                          headers={"User-Agent": "abckasefi-catalog/0.1"}) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as out:
                for chunk in resp.iter_bytes(1 << 20):
                    out.write(chunk)
        os.replace(tmp, path)
    except (httpx.HTTPError, OSError):
        _remove(tmp)
        raise
    return path


def rows(path):
    """Yield the data rows of a gzipped IMDb TSV (header skipped)."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        next(handle)
        for line in handle:
            yield line.rstrip("\n").split("\t")


def load_votes(path):
    """tconst -> (rating, votes); titles below the lowest useful vote count are dropped to save memory."""
    out = {}
    floor = min(config.MIN_VOTES, config.LOCAL_MIN_VOTES)
    for tid, rating, votes in rows(path):
        if votes.isdigit() and rating != "\\N" and int(votes) >= floor:
            out[tid] = (float(rating), int(votes))
    return out


def load_titles(path, known):
    """tconst -> (type, primaryTitle, year, genres) for non-adult movies and series present in `known`."""
    out = {}
    for r in rows(path):
        kind = KINDS.get(r[1])
        if kind and r[4] != "1" and r[0] in known:
            out[r[0]] = (kind, r[2], int(r[5]) if r[5].isdigit() else None, "" if r[8] == "\\N" else r[8])
    return out


# lower score wins: IMDb's own display title first, then untyped, then the odd kinds; working titles never
TYPE_RANK = {"imdbDisplay": 0, "\\N": 1, "alternative": 3, "original": 3, "festival": 4, "dvd": 4, "tv": 4, "video": 4}


def _lang_of(region, language):
    """'cs' / 'sk' / 'en' for an aka row, else ''.

    IMDb tags the same Czech title either by region CZ or - for older titles such as The Godfather - only by
    language cs (region CSHH, the old Czechoslovakia), so both must count. English comes from language en
    or the English-speaking regions.
    """
    if region == "CZ" or language == "cs":
        return "cs"
    if region == "SK" or language == "sk":
        return "sk"
    if language == "en" or region in ("XWW", "US", "GB"):
        return "en"
    return ""


def load_names(path, wanted):
    """tconst -> {'cs'|'sk'|'en': best title in that language} for ids in `wanted` (see TYPE_RANK)."""
    best = {}  # (tid, lang) -> (score, name)
    for r in rows(path):
        if r[0] not in wanted:
            continue
        lang = _lang_of(r[3], r[4])
        if not lang:
            continue
        rank = TYPE_RANK.get(r[5].split("\x02")[0])
        if rank is None:  # 'working' and anything unknown
            continue
        # a row carrying BOTH region and language is the most trustworthy; region-only comes next
        score = rank * 2 + (0 if r[4] != "\\N" else 1)
        key = (r[0], lang)
        if key not in best or score < best[key][0]:
            best[key] = (score, r[2])
    out = {}
    for (tid, lang), (_, name) in best.items():
        out.setdefault(tid, {})[lang] = name
    return out


def pick_name(names, original):
    """Default display title: Czech, else Slovak, else the original. Returns (name, is_czsk)."""
    for lang in ("cs", "sk"):
        if names.get(lang):
            return names[lang], True
    return original, False


def _remove(path):
    pathlib.Path(path).unlink(missing_ok=True)


def build(workdir=None):
    """Download, filter and write a fresh catalog.db; returns the number of titles kept."""
    workdir = workdir or os.path.join(config.DATA, "imdb")
    try:
        os.makedirs(workdir, exist_ok=True)
    except OSError:
        log.exception("cannot create %s", workdir)
        raise
    paths = {n: download(n, workdir) for n in FILES}
    origin_ids = origin.fetch_ids()
    if origin_ids is None:  # TMDB off or unreachable: keep the origin flags the live catalog already has
        origin_ids = origin.previous_ids()
    votes = load_votes(paths["title.ratings"])
    titles = load_titles(paths["title.basics"], votes.keys() | origin_ids)
    names = load_names(paths["title.akas"], titles.keys())
    tmp = db.CATALOG + ".new"
    _remove(tmp)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(db.CATALOG_SCHEMA)
        kept = flagged = 0
        for tid, (kind, orig, year, genres) in titles.items():
            rating, nvotes = votes.get(tid, (None, 0))
            tn = names.get(tid, {})
            name, czsk = pick_name(tn, orig)
            # films and series alike come from TMDB, but a film also needs the vote floor (series: any votes);
            # one rule for fresh and previous ids. Below the floor a film is judged like any other title.
            is_origin = tid in origin_ids and (kind == "series" or nvotes >= config.ORIGIN_MOVIE_MIN_VOTES)
            # keep popular titles, plus less-voted ones with a Czech/Slovak name or a CZ/SK origin
            if not is_origin and nvotes < config.MIN_VOTES and not (czsk and nvotes >= config.LOCAL_MIN_VOTES):
                continue
            key = sortkey(name)
            conn.execute("INSERT INTO titles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (tid, kind, name, orig if czsk and orig != name else None, year, rating, nvotes,
                          genres, letter_of(key), key, 1 if czsk else 0,
                          tn.get("cs"), tn.get("sk"), tn.get("en") or orig, skey(name), int(is_origin)))
            kept += 1
            flagged += is_origin
        conn.executescript(db.CATALOG_INDEXES)
        conn.execute("INSERT INTO fts(rowid, name, orig) SELECT rowid, sortkey, "
                     "TRIM(COALESCE(orig,'') || ' ' || COALESCE(name_sk,'') || ' ' || COALESCE(name_en,'')) FROM titles")
        conn.execute("INSERT INTO meta VALUES('built', datetime('now'))")
        conn.execute("INSERT INTO meta VALUES('min_votes', ?)", (str(config.MIN_VOTES),))
        conn.execute("INSERT INTO meta VALUES('origin_built', ?)", (str(flagged),))  # present = origin is filled
        conn.execute("INSERT INTO meta VALUES('origin_movie_min_votes', ?)", (str(config.ORIGIN_MOVIE_MIN_VOTES),))
        conn.commit()
    except sqlite3.Error:
        conn.close()
        _remove(tmp)
        raise
    conn.close()
    os.replace(tmp, db.CATALOG)
    log.info("catalog built: %d titles", kept)
    return kept


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db.init()
    build()
