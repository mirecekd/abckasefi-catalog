# abckasefi-catalog/tests/catalog_fixture.py
"""Helpers that build small throw-away catalog.db files (current or pre-skey schema) inside a tmp dir."""
import sqlite3

from app import db
from app.importer import letter_of
from app.keys import skey, sortkey

OLD_SCHEMA = """
CREATE TABLE titles(
  id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL, orig TEXT, year INT,
  rating REAL, votes INT, genres TEXT, letter TEXT NOT NULL, sortkey TEXT NOT NULL, local INT,
  name_cs TEXT, name_sk TEXT, name_en TEXT);
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX ix_browse ON titles(type, letter, sortkey);
"""


def make_catalog(path, names, kind="movie", old_schema=False):
    """Write a catalog.db at `path` holding one title per name (ids tt9100000, tt9100001, ...; votes descending).

    old_schema=True creates the layout of releases before the skey column. A 'built' meta row is added so the
    app does not consider the file stale."""
    conn = sqlite3.connect(path)
    if old_schema:
        conn.executescript(OLD_SCHEMA)
    else:
        conn.executescript(db.CATALOG_SCHEMA)
        conn.executescript("CREATE INDEX ix_browse ON titles(type, letter, sortkey); CREATE INDEX ix_skey ON titles(type, skey);")
    for n, name in enumerate(names):
        key = sortkey(name)
        row = (f"tt{9100000 + n}", kind, name, None, 2000 + n % 20, 7.0, 1000 - n % 1000, "", letter_of(key), key, 0, None, None, name)
        if old_schema:
            conn.execute("INSERT INTO titles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        else:
            conn.execute("INSERT INTO titles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*row, skey(name)))
    conn.execute("INSERT INTO meta VALUES('built', datetime('now'))")
    conn.commit()
    conn.close()
