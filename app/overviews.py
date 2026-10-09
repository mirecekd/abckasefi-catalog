# abckasefi-catalog/app/overviews.py
"""Plot summaries ("overview") for list items without any network call in the request path.

annotate() fills each item from the TMDB detail cache in cache.db (the `overview{DETAIL_VERSION}:{lang}:{imdb}` entries
written by tmdb_detail.detail) and, for TMDB lists, from the list result itself. schedule() then runs after the
response was sent and fetches the details of items that have no cache entry yet, so the next view shows the plot.
One process-wide pool of 3 workers serves all requests; an id is never queued twice at the same time.
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, db, tmdb_detail
from . import tmdb_client as client

log = logging.getLogger("catalog.overviews")
MAX_PER_REQUEST = 40
WORKERS = 3
RETRY_AFTER_S = 3600  # a title TMDB could not resolve is not asked for again sooner than this
POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="overview")
PENDING = set()  # (imdb id, language code) being fetched or waiting in the pool
_FAILED = {}  # (imdb id, language code) -> time of the failed attempt
_LOCK = threading.Lock()


def _code(lang):
    return lang or config.TMDB_LANG[:2]


def _cached(ids, code):
    """imdb id -> cached {'overview', 'overview_lang', ...} for the ids that have a fresh cache entry."""
    out = {}
    conn = db.connect(db.CACHE)
    try:
        for imdb in ids:
            row = conn.execute("SELECT val, ts FROM kv WHERE key=?",
                               (f"overview{tmdb_detail.DETAIL_VERSION}:{code}:{imdb}",)).fetchone()
            if row and time.time() - row["ts"] < tmdb_detail.DETAIL_TTL:
                try:
                    out[imdb] = json.loads(row["val"])
                except ValueError:
                    log.warning("corrupt overview cache entry for %s ignored", imdb)
    finally:
        conn.close()
    return out


def annotate(items, lang="", fallback=None):
    """Set item['overview'] / item['overview_lang'] from the cache, else from `fallback` (imdb id -> text from the
    TMDB list result), else "". Returns [(imdb id, type)] of items whose plot is unknown and not cached at all."""
    cached = _cached([i["id"] for i in items], _code(lang))
    missing = []
    for item in items:
        entry = cached.get(item["id"])
        text = (entry or {}).get("overview") or ""
        used = (entry or {}).get("overview_lang") or ""
        if not text and fallback and fallback.get(item["id"]):
            text, used = fallback[item["id"]], config.TMDB_LANG[:2]
        item["overview"], item["overview_lang"] = (text, used) if text else ("", "")
        if not text and entry is None:
            missing.append((item["id"], item["type"]))
    return missing


def _fetch(imdb, kind, lang, key):
    try:
        if tmdb_detail.detail(imdb, kind, lang) is None:
            with _LOCK:
                _FAILED[key] = time.time()
    except Exception:  # noqa: BLE001 - a TMDB or cache problem must never reach the user
        log.exception("overview fetch for %s failed", imdb)
        with _LOCK:
            _FAILED[key] = time.time()
    finally:
        with _LOCK:
            PENDING.discard(key)


def schedule(missing, lang=""):
    """Queue the detail fetch of up to MAX_PER_REQUEST ids from annotate(); no TMDB key = nothing happens."""
    if not missing or not client.enabled():
        return
    code, now = _code(lang), time.time()
    for imdb, kind in missing[:MAX_PER_REQUEST]:
        key = (imdb, code)
        with _LOCK:
            if key in PENDING or now - _FAILED.get(key, 0) < RETRY_AFTER_S:
                continue
            if len(_FAILED) > 5000:
                _FAILED.clear()
            PENDING.add(key)
        try:
            POOL.submit(_fetch, imdb, kind, lang, key)
        except RuntimeError:  # pool already shut down (process exit)
            with _LOCK:
                PENDING.discard(key)
            return


def attach(items, background, lang="", fallback=None):
    """annotate() now, schedule() once the response has been sent. `background` is FastAPI's BackgroundTasks."""
    missing = annotate(items, lang, fallback)
    if missing:
        background.add_task(schedule, missing, lang)
