# abckasefi-catalog/app/migrate.py
"""In-place upgrade of an existing catalog.db to the current schema, so a new release needs no full re-import."""
import logging
import os
import sqlite3

from . import db
from .keys import skey

log = logging.getLogger("catalog.migrate")
BATCH = 5000


def add_skey(path=None):
    """Idempotently add titles.skey (filled from sortkey) and its (type, skey) index; returns True if anything changed.

    Does nothing when there is no catalog yet or the column and index already exist. Memory stays flat: the
    rows are rewritten in batches of BATCH."""
    path = path or db.CATALOG
    if not os.path.exists(path):
        return False
    conn = db.connect(path)
    changed = False
    try:
        if "skey" not in {r["name"] for r in conn.execute("PRAGMA table_info(titles)")}:
            conn.execute("ALTER TABLE titles ADD COLUMN skey TEXT NOT NULL DEFAULT ''")
            last = -1
            while True:
                batch = conn.execute("SELECT rowid, sortkey FROM titles WHERE rowid>? ORDER BY rowid LIMIT ?",
                                     (last, BATCH)).fetchall()
                if not batch:
                    break
                conn.executemany("UPDATE titles SET skey=? WHERE rowid=?", [(skey(r["sortkey"]), r["rowid"]) for r in batch])
                last = batch[-1]["rowid"]
            changed = True
        has_index = conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_skey'").fetchone()
        if not has_index:
            conn.execute("CREATE INDEX ix_skey ON titles(type, skey)")
            changed = True
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        log.exception("migration of %s failed", path)
        raise
    finally:
        conn.close()
    if changed:
        log.info("catalog migrated: skey column and index added")
    return changed


def add_origin(path=None):
    """Idempotently add titles.origin (0 until the next import fills it); returns True if the column was added."""
    path = path or db.CATALOG
    if not os.path.exists(path):
        return False
    conn = db.connect(path)
    try:
        if "origin" in {r["name"] for r in conn.execute("PRAGMA table_info(titles)")}:
            return False
        conn.execute("ALTER TABLE titles ADD COLUMN origin INT NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        log.exception("migration of %s failed", path)
        raise
    finally:
        conn.close()
    log.info("catalog migrated: origin column added")
    return True
