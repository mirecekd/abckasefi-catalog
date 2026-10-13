# abckasefi-catalog/tests/test_origin_fetch.py
"""origin.fetch_ids against the canned TMDB: union of series and films, fail-closed on any error."""
import sqlite3

import pytest

from app import importer, origin


@pytest.fixture()
def tmdb(monkeypatch):
    """Origin fetch against the canned TMDB with an empty cache and no retry pause; returns a setter for failures."""
    from app import tmdb_client
    from tests.fake_tmdb import fake_request
    failing = []
    monkeypatch.setattr(origin, "RETRY_S", 0)
    monkeypatch.setattr(tmdb_client.db, "kv_get", lambda key, ttl: None)  # ignore answers cached by the import
    monkeypatch.setattr(tmdb_client.db, "kv_set", lambda key, data: None)
    monkeypatch.setattr(tmdb_client, "request",
                        lambda path, params: None if any(f(path, params) for f in failing) else fake_request(path, params))
    return failing


def test_fetch_ids_is_the_union_of_series_and_films(tmdb):
    assert origin.fetch_ids() == {"tt0000040", "tt0000041", "tt0000007", "tt0000043", "tt0000001",
                                  "tt0000050", "tt0000051", "tt0000052", "tt0000053", "tt0000055", "tt0000056"}


def test_fetch_ids_none_when_a_page_fails(tmdb):
    tmdb.append(lambda path, params: params.get("page") == 2 and path == "/discover/tv")
    assert origin.fetch_ids() is None  # never a half set: the importer then keeps the previous flags


def test_fetch_ids_none_when_a_movie_page_fails(tmdb):
    tmdb.append(lambda path, params: path == "/discover/movie" and params.get("with_origin_country") == "SK")
    assert origin.fetch_ids() is None


def test_failed_external_ids_is_not_no_imdb_id(tmdb):
    calls = []
    tmdb.append(lambda path, params: calls.append(path) is None and path == "/movie/703/external_ids")
    assert origin.fetch_ids() is None  # one film's id call keeps failing: abandon, do not shrink the set
    assert calls.count("/movie/703/external_ids") == 2  # tried, then retried once


def test_failed_external_ids_is_retried_once(tmdb):
    state = {"left": 1}

    def blip(path, params):
        if path == "/tv/601/external_ids" and state["left"]:
            state["left"] -= 1
            return True
        return False
    tmdb.append(blip)
    ids = origin.fetch_ids()
    assert ids is not None and "tt0000040" in ids


def test_fetch_ids_none_above_the_500_page_cap(tmdb, monkeypatch):
    from tests import fake_tmdb
    monkeypatch.setattr(origin, "QUERIES", origin.QUERIES + (("movie", "ZZ", "primary_release_date.asc"),))
    assert fake_tmdb.MOVIE_PAGES["ZZ"] and origin.fetch_ids() is None  # 501 pages: TMDB would refuse the rest


def test_import_keeps_previous_flags_when_external_ids_fail(tmdb, tmp_path, monkeypatch, built):
    tmdb.append(lambda path, params: path == "/movie/703/external_ids")
    monkeypatch.setattr(importer.db, "CATALOG", str(tmp_path / "rebuilt.db"))
    monkeypatch.setattr(origin, "previous_ids", lambda: {"tt0000050", "tt0000052", "tt0000055", "tt0000041"})
    importer.build()
    conn = sqlite3.connect(str(tmp_path / "rebuilt.db"))
    # the fallback ids get the same film floor: tt55 (100 votes) and the series tt41 (30 votes) keep the flag,
    # tt50 (30 votes, no CZ/SK name) leaves the catalog, tt52 (10 votes, SK name) stays as an ordinary film
    assert sorted(r[0] for r in conn.execute("SELECT id FROM titles WHERE origin=1")) == ["tt0000041", "tt0000055"]
    assert conn.execute("SELECT origin FROM titles WHERE id='tt0000052'").fetchone() == (0,)
    assert conn.execute("SELECT 1 FROM titles WHERE id='tt0000050'").fetchone() is None
    assert conn.execute("SELECT v FROM meta WHERE k='origin_built'").fetchone() == ("2",)
    conn.close()
