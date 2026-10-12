# abckasefi-catalog/app/prefixes.py
"""A-Z drill-down queries over titles.skey (index ix_skey on (type, skey)), optionally narrowed by genre and origin.

A prefix is validated by common.check_prefix (only [a-z0-9]) and reaches SQL solely as a bound parameter. Matching
is a closed range: skey >= p AND skey <= p + '{' ('{' sorts after 'z' and no key contains it); an exact match
uses lo == hi == p. The genre is matched as a whole word (instr over ',genres,'), origin=1 keeps Czech/Slovak series;
both arrive as bound parameters, as do the rating prior and the paging. Every query is its own literal statement
(no string building) returning the columns of db.COLS.
"""
from . import db

CHAR_ORDER = "abcdefghijklmnopqrstuvwxyz0123456789"
PRIOR = 1000  # votes of weight the weighted rating gives the "typical" 6.5 (see db.browse)
PRIOR_ORIGIN = 100  # Czech/Slovak titles have few IMDb votes (median 36): a prior of 1000 would flatten them all to 6.5
_BY_NAME = ("SELECT id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en "
            "FROM titles WHERE type=? AND skey>=? AND skey<=? AND (?=0 OR instr(','||genres||',', ?)>0) "
            "AND (?=0 OR origin=1) ORDER BY sortkey, id LIMIT ? OFFSET ?")
_BY_RATING = ("SELECT id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en "
              "FROM titles WHERE type=? AND skey>=? AND skey<=? AND (?=0 OR instr(','||genres||',', ?)>0) "
              "AND (?=0 OR origin=1) "
              "ORDER BY (rating IS NULL OR COALESCE(votes, 0)=0), "
              "(COALESCE(votes, 0)*COALESCE(rating, 0) + ?*6.5) / (COALESCE(votes, 0) + ?) DESC, "
              "votes DESC, sortkey, id LIMIT ? OFFSET ?")
_BY_VOTES = ("SELECT id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en "
             "FROM titles WHERE type=? AND skey>=? AND skey<=? AND (?=0 OR instr(','||genres||',', ?)>0) "
             "AND (?=0 OR origin=1) ORDER BY votes DESC, sortkey, id LIMIT ? OFFSET ?")
_BY_YEAR = ("SELECT id, type, name, orig, year, rating, votes, genres, letter, local, name_cs, name_sk, name_en "
            "FROM titles WHERE type=? AND skey>=? AND skey<=? AND (?=0 OR instr(','||genres||',', ?)>0) "
            "AND (?=0 OR origin=1) ORDER BY year DESC, sortkey, id LIMIT ? OFFSET ?")
_STATEMENTS = {"name": _BY_NAME, "rating": _BY_RATING, "votes": _BY_VOTES, "year": _BY_YEAR}
_COUNT_CHILDREN = ("SELECT substr(skey, ?, 1) AS c, COUNT(*) AS n FROM titles "
                   "WHERE type=? AND skey>=? AND skey<=? AND (?=0 OR instr(','||genres||',', ?)>0) "
                   "AND (?=0 OR origin=1) GROUP BY c")
_COUNT_TOTAL = ("SELECT COUNT(*) FROM titles WHERE type=? AND skey>=? AND skey<=? "
                "AND (?=0 OR instr(','||genres||',', ?)>0) AND (?=0 OR origin=1)")


def _bounds(prefix, exact):
    return (prefix, prefix) if exact else (prefix, prefix + "{")


def _where(kind, prefix, exact, genre, origin):
    """Bound values of the shared WHERE: type, range, genre flag + ',genre,' pattern, origin flag."""
    lo, hi = _bounds(prefix, exact)
    return (kind, lo, hi, 1 if genre else 0, f",{genre},", 1 if origin else 0)


def children(kind, prefix, genre="", origin=False):
    """{'prefix', 'total', 'exact', 'children': [{'prefix', 'n'}]} for a lower-case prefix; None while no catalog."""
    with db.catalog() as conn:
        if conn is None:
            return None
        counts = {r["c"]: r["n"] for r in conn.execute(
            _COUNT_CHILDREN, (len(prefix) + 1, *_where(kind, prefix, False, genre, origin)))}
    kids = [{"prefix": prefix + c, "n": counts[c]} for c in CHAR_ORDER if c in counts]
    exact = counts.get("", 0)
    return {"prefix": prefix, "total": exact + sum(k["n"] for k in kids), "exact": exact, "children": kids}


def titles(kind, prefix, exact, sort, limit, offset, genre="", origin=False):
    """(rows, total) of titles whose skey starts with (or, with `exact`, equals) the prefix; None while no catalog.

    `genre` (canonical IMDb name) and `origin` narrow the set; with origin the weighted rating uses PRIOR_ORIGIN."""
    with db.catalog() as conn:
        if conn is None:
            return None
        where = _where(kind, prefix, exact, genre, origin)
        total = conn.execute(_COUNT_TOTAL, where).fetchone()[0]
        prior = PRIOR_ORIGIN if origin else PRIOR
        tail = (prior, prior, limit, offset) if sort == "rating" else (limit, offset)
        rows = [dict(r) for r in conn.execute(_STATEMENTS[sort], (*where, *tail))]
    return rows, total
