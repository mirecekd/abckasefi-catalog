# abckasefi-catalog/tests/test_skey_migration.py
"""skey normalisation, the importer filling titles.skey, and the in-place migration of an old catalog.db."""
import sqlite3

import pytest

from app import db, migrate
from app.keys import skey
from tests.catalog_fixture import make_catalog

NAMES = ["A Minute", "The Arm!", "Ёж", "Čelisti: Zákon", "7 dní", "Mr. & Mrs. Smith", "Ar"]
EXPECTED = ["aminute", "thearm", "", "celistizakon", "7dni", "mrmrssmith", "ar"]


def _skeys(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT skey FROM titles ORDER BY rowid")]
    finally:
        conn.close()


def _names(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM titles ORDER BY rowid")]
    finally:
        conn.close()


def _has_skey_index(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_skey'").fetchone() is not None
    finally:
        conn.close()


def test_skey_keeps_only_ascii_letters_and_digits():
    assert [skey(n) for n in NAMES] == EXPECTED
    assert skey("aminute") == "aminute"  # idempotent: applied to a stored sortkey as well


def test_fresh_import_fills_skey_and_creates_index(built):
    # the importer (tests/conftest.py built the catalog with it) writes skey and the (type, skey) index
    conn = sqlite3.connect(db.CATALOG)
    try:
        keys = dict(conn.execute("SELECT id, skey FROM titles"))
        assert keys["tt0000012"] == "armageddontime"
        assert keys["tt0000013"] == "prayingforarmageddon"
        assert keys["tt0000020"] == "kmotr"  # Czech display name wins
        assert keys["tt0000005"] == "7dni"
        assert all(k.isalnum() or k == "" for k in keys.values())
        plan = " ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM titles WHERE type=? AND skey>=? AND skey<=?", ("movie", "ar", "ar{")))
    finally:
        conn.close()
    assert _has_skey_index(db.CATALOG) and "ix_skey" in plan


def test_migration_of_old_catalog_without_skey(tmp_path):
    path = str(tmp_path / "catalog.db")
    make_catalog(path, NAMES, old_schema=True)
    with pytest.raises(sqlite3.OperationalError):
        _skeys(path)  # the old layout really has no skey
    assert migrate.add_skey(path) is True
    assert _skeys(path) == EXPECTED
    assert _has_skey_index(path)
    assert _names(path) == NAMES  # nothing else was touched


def test_migration_is_idempotent_and_ignores_missing_or_current_catalogs(tmp_path):
    path = str(tmp_path / "catalog.db")
    assert migrate.add_skey(path) is False and not (tmp_path / "catalog.db").exists()
    make_catalog(path, NAMES, old_schema=True)
    assert migrate.add_skey(path) is True
    assert migrate.add_skey(path) is False
    current = str(tmp_path / "current.db")
    make_catalog(current, NAMES)
    assert migrate.add_skey(current) is False
    assert _skeys(current) == EXPECTED


def test_migration_restores_a_dropped_index(tmp_path):
    path = str(tmp_path / "catalog.db")
    make_catalog(path, NAMES)
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX ix_skey")
    conn.commit()
    conn.close()
    assert migrate.add_skey(path) is True and _has_skey_index(path)


def test_migration_handles_more_rows_than_one_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "BATCH", 7)
    path = str(tmp_path / "catalog.db")
    names = [f"Title {n}" for n in range(30)]
    make_catalog(path, names, old_schema=True)
    migrate.add_skey(path)
    assert _skeys(path) == [f"title{n}" for n in range(30)]
