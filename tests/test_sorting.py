# abckasefi-catalog/tests/test_sorting.py
"""Weighted rating (Bayesian, M=1000, C=6.5), votes order and sort validation of /v1/titles and /v1/search.

Letter Q holds Quorum Obscure (9.4 from 20 votes), Quasar Epic (8.8 from 2 000 000) and Quartz Middle (7.0 from 5 000).
"""
import sqlite3

import pytest

from app import db


def _browse(client, **params):
    r = client.get("/v1/titles", params={"type": "movie", "letter": "Q", **params})
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def _search(client, **params):
    r = client.get("/v1/search", params={"q": "qu", "type": "movie", **params})
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def test_browse_default_is_name_order(client):
    assert _browse(client) == ["tt0000032", "tt0000031", "tt0000030"]  # quartz, quasar, quorum
    assert _browse(client, sort="name") == _browse(client)


def test_browse_rating_is_weighted_by_votes(client):
    # raw order would put 9.4 from 20 votes first; weighted: Quasar ~8.8, Quartz ~6.92, Quorum ~6.56
    assert _browse(client, sort="rating") == ["tt0000031", "tt0000032", "tt0000030"]


def test_browse_votes_orders_by_vote_count(client):
    assert _browse(client, sort="votes") == ["tt0000031", "tt0000032", "tt0000030"]
    assert _browse(client, sort="votes", per_page=1) == ["tt0000031"]


def test_browse_year_orders_newest_first(client):
    assert _browse(client, sort="year") == ["tt0000032", "tt0000030", "tt0000031"]


def test_browse_rating_puts_missing_rating_or_votes_last(tmp_path, monkeypatch):
    # the importer never writes NULL ratings, but the SQL must still rank them after a poor rating with many votes
    path = str(tmp_path / "catalog.db")
    conn = sqlite3.connect(path)
    conn.executescript(db.CATALOG_SCHEMA)
    rows = [("a", 2.0, 100000), ("b", None, None), ("c", 9.9, 0), ("d", 5.0, None), ("e", 8.0, 3000)]
    conn.executemany("INSERT INTO titles(id, type, name, rating, votes, letter, sortkey) "
                     "VALUES(?, 'movie', ?, ?, ?, 'Z', ?)", [(i, i, r, v, i) for i, r, v in rows])
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "CATALOG", path)
    result = db.browse("movie", "Z", "rating", 10, 0)
    assert result is not None and [r["id"] for r in result] == ["e", "a", "c", "b", "d"]  # then votes DESC (NULL last), then name


@pytest.mark.parametrize("sort", ["evil;drop", "", "popular", "Rating", "tmdb"])
def test_browse_unknown_sort_is_400(client, sort):
    r = client.get("/v1/titles", params={"type": "movie", "letter": "Q", "sort": sort})
    assert r.status_code == 400
    assert "sort must be one of name, rating, votes, year" in r.text


def test_search_rating_is_weighted_and_votes_aliases_popular(client):
    assert _search(client, sort="rating") == ["tt0000031", "tt0000032", "tt0000030"]
    assert _search(client, sort="votes") == _search(client, sort="popular") == _search(client)
    assert _search(client)[:2] == ["tt0000031", "tt0000032"]


def test_search_rating_limit_keeps_best_weighted_hit(client):
    assert _search(client, sort="rating", limit=1) == ["tt0000031"]
