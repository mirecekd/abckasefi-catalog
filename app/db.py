# abckasefi-catalog/app/db.py
"""SQLite access. catalog.db is rebuilt by the importer and swapped atomically; cache.db (TMDB) persists.

All SQL is literal text with bound parameters. Sort orders are separate literal statements chosen
from a fixed mapping, never built from user input.
"""
import contextlib
import json
import logging
import os
import sqlite3
import time

from . import config

log = logging.getLogger("catalog.db")
CATALOG = os.path.join(config.DATA, "catalog.db")
CACHE = os.path.join(config.DATA, "cache.db")

CATALOG_SCHEMA = """
CREATE TABLE titles(
  id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, orig TEXT, year INT,
  rating REAL, votes INT, genres TEXT, letter TEXT NOT NULL, sortkey TEXT NOT NULL, local INT,
  name_cs TEXT, name_sk TEXT, name_en TEXT, skey TEXT NOT NULL DEFAULT '');
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
"""
CATALOG_INDEXES = """
CREATE INDEX ix_browse ON titles(type, letter, sortkey);
CREATE INDEX ix_skey ON titles(type, skey);
CREATE VIRTUAL TABLE fts USING fts5(name, orig, content='', tokenize='unicode61 remove_diacritics 2');
"""

SORTS = ("name", "rating", "votes", "year")  # accepted by /v1/titles
COLS = "id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en"


def connect(path):
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    """Create the data dir and the persistent TMDB cache table."""
    try:
        os.makedirs(config.DATA, exist_ok=True)
        conn = connect(CACHE)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, val TEXT, ts REAL)")
        conn.commit()
        conn.close()
    except (OSError, sqlite3.Error):
        log.exception("cannot initialise %s", config.DATA)
        raise


def ready():
    return os.path.exists(CATALOG)


@contextlib.contextmanager
def catalog():
    """Read connection to catalog.db, or None while the first import has not produced the file."""
    if not ready():
        yield None
        return
    conn = connect(CATALOG)
    try:
        yield conn
    finally:
        conn.close()


def _rows(cursor):
    return [dict(r) for r in cursor]


def stats():
    with catalog() as conn:
        if conn is None:
            return {"counts": {}, "meta": {}}
        counts = {r["type"]: r["n"] for r in conn.execute("SELECT type, COUNT(*) AS n FROM titles GROUP BY type")}
        meta = {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM meta")}
    return {"counts": counts, "meta": meta}


def letters(kind):
    with catalog() as conn:
        if conn is None:
            return None
        return _rows(conn.execute(
            "SELECT letter, COUNT(*) AS n FROM titles WHERE type=? GROUP BY letter ORDER BY letter='0-9', letter",
            (kind,)))


def browse(kind, letter, sort, limit, offset):
    """One letter of one type. 'rating' is the Bayesian weighted rating (votes*rating + 1000*6.5) / (votes + 1000);
    titles without a rating or votes come last."""
    with catalog() as conn:
        if conn is None:
            return None
        if sort == "rating":
            cur = conn.execute(
                "SELECT " + COLS + " FROM titles "
                "WHERE type=? AND letter=? ORDER BY (rating IS NULL OR COALESCE(votes, 0)=0), "
                "(COALESCE(votes, 0)*COALESCE(rating, 0) + 1000*6.5) / (COALESCE(votes, 0) + 1000) DESC, "
                "votes DESC, sortkey LIMIT ? OFFSET ?",
                (kind, letter, limit, offset))
        elif sort == "votes":
            cur = conn.execute(
                "SELECT " + COLS + " FROM titles "
                "WHERE type=? AND letter=? ORDER BY votes DESC, sortkey LIMIT ? OFFSET ?",
                (kind, letter, limit, offset))
        elif sort == "year":
            cur = conn.execute(
                "SELECT " + COLS + " FROM titles "
                "WHERE type=? AND letter=? ORDER BY year DESC, sortkey LIMIT ? OFFSET ?",
                (kind, letter, limit, offset))
        else:
            cur = conn.execute(
                "SELECT " + COLS + " FROM titles "
                "WHERE type=? AND letter=? ORDER BY sortkey LIMIT ? OFFSET ?",
                (kind, letter, limit, offset))
        return _rows(cur)


def letter_total(kind, letter):
    """Number of titles under one A-Z letter; None while there is no catalog."""
    with catalog() as conn:
        if conn is None:
            return None
        return conn.execute("SELECT COUNT(*) FROM titles WHERE type=? AND letter=?", (kind, letter)).fetchone()[0]


def top(kind, min_votes, limit, offset):
    with catalog() as conn:
        if conn is None:
            return None
        return _rows(conn.execute(
            "SELECT " + COLS + " FROM titles "
            "WHERE type=? AND votes>=? ORDER BY rating DESC, votes DESC LIMIT ? OFFSET ?",
            (kind, min_votes, limit, offset)))


def search(match, kind, sort, limit):
    """`match` must already be a safe FTS5 expression (see main.fts_query); kind '' = both types.

    sort: 'popular' or its alias 'votes' (most voted first, default for search), 'name', 'rating' (the same
    Bayesian weighted rating as browse) or 'year'. Each order is its own
    literal statement. `limit` applies after ordering, so it is the best N matches under that order.
    """
    cols = ", ".join("t." + c for c in COLS.split(", "))
    with catalog() as conn:
        if conn is None:
            return None
        args = (match, kind, kind, limit)
        base = f"SELECT {cols} FROM fts JOIN titles t ON t.rowid=fts.rowid WHERE fts MATCH ? AND (?='' OR t.type=?) "
        if sort == "rating":
            cur = conn.execute(
                base + "ORDER BY (t.rating IS NULL OR COALESCE(t.votes, 0)=0), "
                "(COALESCE(t.votes, 0)*COALESCE(t.rating, 0) + 1000*6.5) / (COALESCE(t.votes, 0) + 1000) DESC, "
                "t.votes DESC LIMIT ?", args)
        elif sort == "year":
            cur = conn.execute(base + "ORDER BY t.year DESC NULLS LAST, t.votes DESC LIMIT ?", args)
        elif sort == "name":
            cur = conn.execute(base + "ORDER BY t.sortkey, t.year LIMIT ?", args)
        else:
            cur = conn.execute(base + "ORDER BY t.votes DESC LIMIT ?", args)
        return _rows(cur)


def get(title_id):
    with catalog() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT " + COLS + " FROM titles WHERE id=?",
            (title_id,)).fetchone()
    return dict(row) if row else None


def kv_get(key, ttl):
    """Cached JSON value younger than ttl seconds, else None."""
    conn = connect(CACHE)
    try:
        row = conn.execute("SELECT val, ts FROM kv WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    if not row or time.time() - row["ts"] >= ttl:
        return None
    try:
        return json.loads(row["val"])
    except ValueError:
        log.warning("corrupt cache entry %s dropped", key)
        return None


def kv_set(key, value):
    conn = connect(CACHE)
    try:
        conn.execute("INSERT OR REPLACE INTO kv VALUES(?,?,?)", (key, json.dumps(value), time.time()))
        conn.commit()
    finally:
        conn.close()
