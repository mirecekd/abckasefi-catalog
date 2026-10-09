# abckasefi-catalog/tests/test_prefixes.py
"""GET /v1/prefixes and the prefix / exact / total parameters of GET /v1/titles."""
import pytest

from app import config, db
from tests.catalog_fixture import make_catalog

NAMES = ["Ar", "Arm", "Arm", "Armageddon", "Arch", "The Arrow", "Ara", "A Minute", "Mr. Smith", "Ёж", "Žába", "7 dní", "300",
         "Star Wars: Episode IV", "Star Wars", "Starship"]


@pytest.fixture()
def small(tmp_path, monkeypatch, client):
    path = str(tmp_path / "catalog.db")
    make_catalog(path, NAMES)
    monkeypatch.setattr(db, "CATALOG", path)
    return client


def _prefixes(client, prefix="", kind="movie"):
    r = client.get("/v1/prefixes", params={"type": kind, "prefix": prefix})
    assert r.status_code == 200, r.text
    return r.json()


def test_root_counts(small):
    r = _prefixes(small)
    assert r["total"] == len(NAMES) and r["exact"] == 1  # "Ёж" has an empty key
    assert [c["prefix"] for c in r["children"]] == ["a", "m", "s", "t", "z", "3", "7"]  # letters, then digits
    counts = {c["prefix"]: c["n"] for c in r["children"]}
    # "The Arrow" -> thearrow (t), "A Minute" -> aminute (a)
    assert counts == {"a": 7, "m": 1, "s": 3, "t": 1, "z": 1, "3": 1, "7": 1}
    assert sum(counts.values()) + r["exact"] == r["total"]


def test_recursive_split_ar_to_arm(small):
    ar = _prefixes(small, "ar")
    assert (ar["prefix"], ar["total"], ar["exact"]) == ("ar", 6, 1)
    assert ar["children"] == [{"prefix": "ara", "n": 1}, {"prefix": "arc", "n": 1}, {"prefix": "arm", "n": 3}]
    arm = _prefixes(small, "ARM")  # upper case is accepted and normalised
    assert (arm["prefix"], arm["total"], arm["exact"]) == ("arm", 3, 2)
    assert arm["children"] == [{"prefix": "arma", "n": 1}]
    leaf = _prefixes(small, "armageddon")
    assert (leaf["total"], leaf["exact"], leaf["children"]) == (1, 1, [])


@pytest.mark.parametrize("prefix", ["", "a", "ar", "arm", "s", "star", "starwars", "7", "zzz", "t"])
def test_children_sum_plus_exact_equals_total(small, prefix):
    r = _prefixes(small, prefix)
    assert sum(c["n"] for c in r["children"]) + r["exact"] == r["total"]
    for child in r["children"]:
        assert child["prefix"].startswith(prefix) and len(child["prefix"]) == len(prefix) + 1
        assert _prefixes(small, child["prefix"])["total"] == child["n"]


def test_the_and_punctuation_are_part_of_the_key(small):
    assert _prefixes(small, "the")["total"] == 1
    assert _prefixes(small, "thearrow")["exact"] == 1
    assert _prefixes(small, "aminute")["exact"] == 1  # space dropped
    assert _prefixes(small, "mrsmith")["exact"] == 1  # "Mr. Smith"
    assert _prefixes(small, "starwars")["total"] == 2  # "Star Wars" and "Star Wars: Episode IV" ("starwarsepisodeiv")
    assert _prefixes(small, "starwarse")["total"] == 1
    assert _prefixes(small, "7dni")["exact"] == 1  # "7 dní" without diacritics and space


def test_type_separates_movies_from_series(small):
    assert _prefixes(small, "ar", kind="series") == {"prefix": "ar", "total": 0, "exact": 0, "children": []}


@pytest.mark.parametrize("params", [{"prefix": "a b"}, {"prefix": "a-"}, {"prefix": "a%"}, {"prefix": "x" * 11},
                                    {"prefix": "'; DROP TABLE titles;--"}, {"prefix": "ař"}, {"type": "bogus"}])
def test_invalid_prefix_or_type_is_400(small, params):
    assert small.get("/v1/prefixes", params={"type": "movie", **params}).status_code == 400
    assert db.stats()["counts"]["movie"] == len(NAMES)


def test_prefixes_need_the_token(small, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "s3cret")
    assert small.get("/v1/prefixes?type=movie&prefix=ar").status_code == 401
    assert small.get("/v1/prefixes?type=movie&prefix=ar&token=s3cret").status_code == 200


def test_prefixes_on_the_imported_fixture_catalog(client):
    r = client.get("/v1/prefixes", params={"type": "movie", "prefix": "ar"}).json()
    assert (r["total"], r["exact"], r["children"]) == (2, 0, [{"prefix": "arm", "n": 2}])  # Armageddon, Armageddon Time
    assert client.get("/v1/prefixes", params={"type": "series", "prefix": "per"}).json()["total"] == 1


def _titles(client, **params):
    r = client.get("/v1/titles", params={"type": "movie", **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_titles_by_prefix_and_total(small):
    body = _titles(small, prefix="AR", per_page=2)
    assert body["total"] == 6 and body["has_more"] is True and len(body["items"]) == 2
    assert [i["title"] for i in body["items"]] == ["Ar", "Ara"]  # by sort key, letter parameter is ignored
    names = [i["title"] for p in (1, 2, 3) for i in _titles(small, prefix="ar", per_page=2, page=p, letter="Z")["items"]]
    assert names == ["Ar", "Ara", "Arch", "Arm", "Arm", "Armageddon"]


def test_titles_exact_keeps_only_equal_keys(small):
    body = _titles(small, prefix="arm", exact=1)
    assert body["total"] == 2 and [i["title"] for i in body["items"]] == ["Arm", "Arm"]
    assert _titles(small, prefix="armageddon", exact=1)["total"] == 1
    assert _titles(small, prefix="arma", exact=1)["total"] == 0


def test_titles_exact_with_empty_prefix_lists_titles_with_an_empty_key(small):
    body = _titles(small, exact=1)
    assert [i["title"] for i in body["items"]] == ["Ёж"] and body["total"] == 1


def test_titles_prefix_respects_sort(small):
    by_votes = [i["title"] for i in _titles(small, prefix="st", sort="votes")["items"]]
    assert by_votes[0] == "Star Wars: Episode IV"  # inserted first = most votes
    assert _titles(small, prefix="st", sort="year")["total"] == 3
    assert small.get("/v1/titles", params={"prefix": "st", "sort": "bogus"}).status_code == 400


@pytest.mark.parametrize("prefix", ["a b", "ab-", "x" * 11])
def test_titles_invalid_prefix_is_400(small, prefix):
    assert small.get("/v1/titles", params={"type": "movie", "prefix": prefix}).status_code == 400
    assert small.get("/v1/titles", params={"type": "movie", "exact": 2}).status_code == 422


def test_titles_without_prefix_is_unchanged_but_reports_total(client):
    body = client.get("/v1/titles?type=movie&letter=Q").json()
    assert [i["id"] for i in body["items"]] == ["tt0000032", "tt0000031", "tt0000030"]
    assert (body["total"], body["has_more"], body["page"], body["per_page"]) == (3, False, 1, 50)
    assert client.get("/v1/titles?type=movie&letter=Q&per_page=2").json()["total"] == 3
    assert client.get("/v1/titles?type=movie&letter=0-9").json()["total"] == 1
