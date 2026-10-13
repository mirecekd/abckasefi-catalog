# abckasefi-catalog/tests/test_origin_floor.py
"""ORIGIN_MOVIE_MIN_VOTES: Czech/Slovak films need the vote floor to be flagged origin, series do not."""
import sqlite3

from app import db


def _titles(client, **params):
    r = client.get("/v1/titles", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ids(body):
    return [i["id"] for i in body["items"]]


def _origin_of(title_id):
    conn = sqlite3.connect(db.CATALOG)
    try:
        return conn.execute("SELECT origin FROM titles WHERE id=?", (title_id,)).fetchone()[0]
    finally:
        conn.close()


def test_importer_flags_origin_and_keeps_low_vote_origin_series(built):
    assert db.get("tt0000040") is not None and db.get("tt0000041") is not None  # 100 / 30 votes, kept for origin
    assert db.get("tt0000042") is None  # same votes, no origin: dropped as before
    flagged = {i for i in ("tt0000007", "tt0000040", "tt0000041", "tt0000043") if _origin_of(i) == 1}
    assert flagged == {"tt0000007", "tt0000040", "tt0000041", "tt0000043"}  # series: any votes, even no rating row
    assert (db.get("tt0000043") or {})["rating"] is None
    assert _origin_of("tt0000020") == 0 and _origin_of("tt0000003") == 0  # no CZ/SK/XC production
    # 4 series + 2 films (tt1, tt55); TMDB 606 and 705 have no IMDb id, 702 is listed under CZ and SK, 707 repeats tt0000040
    assert db.stats()["meta"]["origin_built"] == "6"
    assert db.stats()["meta"]["origin_movie_min_votes"] == "100"


def test_origin_films_need_the_vote_floor(built):
    for tid, votes in {"tt0000001": 5000, "tt0000055": 100}.items():  # at or above the floor (100 votes exactly)
        row = db.get(tid)
        assert row is not None and row["votes"] == votes and _origin_of(tid) == 1
    assert db.get("tt0000056") is None  # origin film, 99 votes, no CZ/SK name: not origin, not in the catalog
    assert db.get("tt0000050") is None  # 30 votes, no CZ/SK name
    assert db.get("tt0000053") is None  # 6 votes, no CZ/SK name
    assert db.get("tt0000051") is None  # no rating row at all
    assert db.get("tt0000054") is None  # no origin either
    assert _origin_of("tt0000040") == 1  # a title TMDB lists on both endpoints is flagged once


def test_origin_film_below_the_floor_with_a_czsk_name_is_an_ordinary_film(built):
    row = db.get("tt0000052")  # SK origin, 10 votes >= LOCAL_MIN_VOTES, has a Slovak name
    assert row is not None and row["votes"] == 10 and row["name"] == "Slovenský Xenón"
    assert _origin_of("tt0000052") == 0


def test_origin_films_filter_and_rating_order(client):
    body = _titles(client, type="movie", origin="czsk", sort="rating")
    assert _ids(body) == ["tt0000001", "tt0000055"]  # 8.2 / 5000 votes first; only films at or above the 100-vote floor
    assert body["total"] == 2
    assert "tt0000020" not in _ids(body)  # The Godfather is not Czech or Slovak
    assert "tt0000052" not in _ids(body)  # in the catalog (SK name, 10 votes) but below the floor
