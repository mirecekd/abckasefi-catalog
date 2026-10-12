# abckasefi-catalog/tests/test_genre_origin.py
"""Genre and CZ/SK-origin filters: importer flag, /v1/titles, /v1/prefixes, /v1/genres, the origin migration."""
import sqlite3

import pytest

from app import config, db, importer, migrate, origin
from tests.catalog_fixture import make_catalog

NAMES = ["Alfa", "Beta", "Gama", "Delta"]
GENRES = {"Alfa": "Drama,Sci-Fi", "Beta": "Sci-Fi", "Gama": "Drama", "Delta": "Sci-Fi,Thriller"}


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


def test_importer_flags_origin_and_keeps_low_vote_origin_titles(built):
    assert db.get("tt0000040") is not None and db.get("tt0000041") is not None  # 100 / 30 votes, kept for origin
    assert db.get("tt0000042") is None  # same votes, no origin: dropped as before
    flagged = {i for i in ("tt0000007", "tt0000040", "tt0000041", "tt0000043") if _origin_of(i) == 1}
    assert flagged == {"tt0000007", "tt0000040", "tt0000041", "tt0000043"}
    assert _origin_of("tt0000020") == 0 and _origin_of("tt0000003") == 0  # no CZ/SK/XC production
    # 4 series + 5 films: TMDB 606 and 705 have no IMDb id, 702 is listed under CZ and SK, 707 repeats tt0000040
    assert db.stats()["meta"]["origin_built"] == "9"


def test_films_are_flagged_and_kept_below_min_votes(built):
    films = {"tt0000001": 5000, "tt0000050": 30, "tt0000051": 0, "tt0000052": 10, "tt0000053": 6}
    for tid, votes in films.items():  # tt0000001 comes from the CZ query, the others from CZ, SK and XC
        row = db.get(tid)
        assert row is not None and row["votes"] == votes and _origin_of(tid) == 1
    assert (db.get("tt0000051") or {})["rating"] is None  # no IMDb rating row at all
    assert db.get("tt0000054") is None  # same votes as tt0000050, no origin: dropped
    assert _origin_of("tt0000040") == 1  # a title TMDB lists on both endpoints is flagged once


def test_origin_only_filter(client):
    body = _titles(client, type="series", origin="czsk", sort="name")
    assert sorted(_ids(body)) == ["tt0000007", "tt0000040", "tt0000041", "tt0000043"]
    assert body["total"] == 4
    assert "tt0000003" not in _ids(body)  # Breaking Bad has no CZ/SK origin


def test_origin_films_filter_and_rating_order(client):
    body = _titles(client, type="movie", origin="czsk", sort="rating")
    assert sorted(_ids(body)) == ["tt0000001", "tt0000050", "tt0000051", "tt0000052", "tt0000053"]
    assert _ids(body)[0] == "tt0000001" and _ids(body)[-1] == "tt0000051"  # 8.2 / 5000 votes first, no rating last
    assert "tt0000020" not in _ids(body)  # The Godfather is not Czech or Slovak


def test_origin_genres_for_films(client):
    body = client.get("/v1/genres", params={"type": "movie", "origin": "czsk"}).json()
    assert {g["genre"]: g["n"] for g in body} == {"Drama": 4, "Comedy": 2, "Crime": 1}


def test_origin_title_without_rating_comes_last_by_rating(client):
    ids = _ids(_titles(client, type="series", origin="czsk", sort="rating"))
    assert ids[-1] == "tt0000043" and ids[0] == "tt0000040"


def test_genre_alone_is_flat_list_best_first(client):
    body = _titles(client, type="movie", genre="sci-fi", sort="rating")  # case-insensitive, no letter, no prefix
    assert _ids(body) == ["tt0000031", "tt0000014"]  # 8.8 / 2M votes ahead of 2.1 / 1284
    assert body["total"] == 2


def test_genre_is_a_whole_word(client):
    assert _titles(client, type="movie", genre="Drama")["total"] >= 1
    assert _titles(client, type="series", genre="Crime")["total"] == 2  # Breaking Bad + tt41, not a substring match


def test_genre_and_origin_combine(client):
    body = _titles(client, type="series", genre="Sci-Fi", origin="czsk")
    assert _ids(body) == ["tt0000040"]


def test_bad_genre_and_origin_are_400(client):
    assert client.get("/v1/titles", params={"type": "movie", "genre": "Sci-Fi'; DROP"}).status_code == 400
    assert client.get("/v1/titles", params={"type": "movie", "genre": "Docu"}).status_code == 400
    assert client.get("/v1/titles", params={"type": "series", "origin": "us"}).status_code == 400
    assert client.get("/v1/prefixes", params={"type": "series", "genre": "x"}).status_code == 400


def test_titles_without_filter_still_need_a_letter(client):
    assert _titles(client, type="movie", letter="Q")["total"] == 3  # unchanged behaviour


def test_prefixes_respect_filters(client):
    r = client.get("/v1/prefixes", params={"type": "series", "origin": "czsk"}).json()
    assert r["total"] == 4
    both = client.get("/v1/prefixes", params={"type": "series", "origin": "czsk", "genre": "Crime"}).json()
    assert both["total"] == 1 and [c["prefix"] for c in both["children"]] == ["s"]


def test_genres_endpoint(client):
    body = client.get("/v1/genres", params={"type": "series"}).json()
    counts = {g["genre"]: g["n"] for g in body}
    assert counts["Drama"] >= 3 and counts["Crime"] == 2 and counts["Sci-Fi"] == 1
    assert [g["n"] for g in body] == sorted((g["n"] for g in body), reverse=True)
    czsk = {g["genre"] for g in client.get("/v1/genres", params={"type": "series", "origin": "czsk"}).json()}
    assert czsk == {"Drama", "Sci-Fi", "Crime"}
    assert client.get("/v1/genres", params={"type": "tv"}).status_code == 400


@pytest.fixture()
def small(tmp_path, monkeypatch, client):
    path = str(tmp_path / "catalog.db")
    make_catalog(path, NAMES, genres=GENRES, origin=("Gama", "Delta"))
    monkeypatch.setattr(db, "CATALOG", path)
    return client


def test_weighted_rating_prior_is_lower_for_origin_titles(small):
    """make_catalog gives every title rating 7.0 and descending votes (1000, 999, ...): same rating, so votes decide."""
    assert _ids(_titles(small, type="movie", genre="Sci-Fi", sort="rating")) == ["tt9100000", "tt9100001", "tt9100003"]
    assert _ids(_titles(small, type="movie", origin="czsk", sort="rating")) == ["tt9100002", "tt9100003"]


def test_rating_prior_100_ranks_few_votes_higher_than_prior_1000():
    from app import prefixes
    w = lambda rating, votes, prior: (votes * rating + prior * 6.5) / (votes + prior)  # noqa: E731 - mirrors the SQL
    small_hit, big_ok = (9.0, 40), (7.5, 3000)
    assert w(*small_hit, prefixes.PRIOR) < w(*big_ok, prefixes.PRIOR)
    assert w(*small_hit, prefixes.PRIOR_ORIGIN) > w(7.0, 80, prefixes.PRIOR_ORIGIN)  # 9.0 / 40 beats 7.0 / 80 again


def test_add_origin_migration(tmp_path):
    path = str(tmp_path / "catalog.db")
    make_catalog(path, NAMES)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE titles DROP COLUMN origin")  # a catalog made before the column existed
    conn.commit()
    conn.close()
    assert migrate.add_origin(path) is True
    assert migrate.add_origin(path) is False  # idempotent
    conn = sqlite3.connect(path)
    assert {r[0] for r in conn.execute("SELECT origin FROM titles")} == {0}
    conn.close()


def test_add_origin_without_catalog_does_nothing(tmp_path):
    assert migrate.add_origin(str(tmp_path / "missing.db")) is False


def test_fetch_ids_none_without_tmdb(monkeypatch):
    monkeypatch.setattr(config, "TMDB_KEY", "")
    assert origin.fetch_ids() is None


def test_flat_lists_the_whole_type_best_rated_first(client):
    """flat=1 = no letter, no filter: what the web UI's "Vse" button and 'sort by rating' use."""
    body = _titles(client, type="movie", flat=1, sort="rating", per_page=3)
    assert _ids(body)[:2] == ["tt0000020", "tt0000031"]  # Godfather 9.2 then Quasar Epic 8.8 (letter Q): any letter
    assert body["total"] == db.stats()["counts"]["movie"] and body["has_more"] is True
    assert _titles(client, type="movie", flat=1, sort="name", per_page=2)["items"][0]["id"] != body["items"][0]["id"]
    assert client.get("/v1/titles", params={"type": "movie", "flat": 2}).status_code == 422
