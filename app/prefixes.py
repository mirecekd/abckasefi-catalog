# abckasefi-catalog/app/prefixes.py
"""A-Z drill-down queries over titles.skey (index ix_skey on (type, skey)).

A prefix is validated by common.check_prefix (only [a-z0-9]) and reaches SQL solely as a bound parameter. Matching
is a closed range: skey >= p AND skey <= p + '{' ('{' sorts after 'z' and no key contains it); an exact match
uses lo == hi == p. Every sort order is its own literal statement returning the columns of db.COLS.
"""
from . import db

CHAR_ORDER = "abcdefghijklmnopqrstuvwxyz0123456789"
_BY_NAME = ("SELECT id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en "
            "FROM titles WHERE type=? AND skey>=? AND skey<=? ORDER BY sortkey, id LIMIT ? OFFSET ?")
_BY_RATING = ("SELECT id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en "
              "FROM titles WHERE type=? AND skey>=? AND skey<=? "
              "ORDER BY (rating IS NULL OR COALESCE(votes, 0)=0), "
              "(COALESCE(votes, 0)*COALESCE(rating, 0) + 1000*6.5) / (COALESCE(votes, 0) + 1000) DESC, "
              "votes DESC, sortkey, id LIMIT ? OFFSET ?")
_BY_VOTES = ("SELECT id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en "
             "FROM titles WHERE type=? AND skey>=? AND skey<=? ORDER BY votes DESC, sortkey, id LIMIT ? OFFSET ?")
_BY_YEAR = ("SELECT id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en "
            "FROM titles WHERE type=? AND skey>=? AND skey<=? ORDER BY year DESC, sortkey, id LIMIT ? OFFSET ?")
_STATEMENTS = {"name": _BY_NAME, "rating": _BY_RATING, "votes": _BY_VOTES, "year": _BY_YEAR}


def _bounds(prefix, exact):
    return (prefix, prefix) if exact else (prefix, prefix + "{")


def children(kind, prefix):
    """{'prefix', 'total', 'exact', 'children': [{'prefix', 'n'}]} for a lower-case prefix; None while no catalog."""
    with db.catalog() as conn:
        if conn is None:
            return None
        lo, hi = _bounds(prefix, False)
        counts = {r["c"]: r["n"] for r in conn.execute(
            "SELECT substr(skey, ?, 1) AS c, COUNT(*) AS n FROM titles "
            "WHERE type=? AND skey>=? AND skey<=? GROUP BY c", (len(prefix) + 1, kind, lo, hi))}
    kids = [{"prefix": prefix + c, "n": counts[c]} for c in CHAR_ORDER if c in counts]
    exact = counts.get("", 0)
    return {"prefix": prefix, "total": exact + sum(k["n"] for k in kids), "exact": exact, "children": kids}


def titles(kind, prefix, exact, sort, limit, offset):
    """(rows, total) of titles whose skey starts with (or, with `exact`, equals) the prefix; None while no catalog."""
    with db.catalog() as conn:
        if conn is None:
            return None
        lo, hi = _bounds(prefix, exact)
        total = conn.execute("SELECT COUNT(*) FROM titles WHERE type=? AND skey>=? AND skey<=?",
                             (kind, lo, hi)).fetchone()[0]
        rows = [dict(r) for r in conn.execute(_STATEMENTS[sort], (kind, lo, hi, limit, offset))]
    return rows, total
