# abckasefi-catalog/tests/test_browse_search.py
"""Letters, browse and full-text search endpoints, including `has_more`."""
import pytest

from app import db


def test_letters_order_digits_last(client):
    letters = [x["letter"] for x in client.get("/v1/letters?type=movie").json()]
    assert letters[-1] == "0-9" and letters[:-1] == sorted(letters[:-1])


def test_browse_sorted_and_czech_chars_sort_as_base_letter(client):
    # "Český film" must land under C, before "Perníkový"
    items = client.get("/v1/titles?type=movie&letter=C").json()["items"]
    assert [i["title"] for i in items] == ["Český film"]


def test_search_ignores_diacritics_and_prefix(client):
    assert [i["title"] for i in client.get("/v1/search?q=pelisk").json()["items"]] == ["Pelíšky"]
    assert [i["id"] for i in client.get("/v1/search?q=perni&type=series").json()["items"]] == ["tt0000003"]


@pytest.mark.parametrize("q", ['"', "a OR b NEAR(", "x*y", "\\", "AND", "'; DROP TABLE titles;--"])
def test_search_survives_hostile_input(client, q):
    r = client.get("/v1/search", params={"q": q + "zz"})
    assert r.status_code == 200
    assert db.stats()["counts"]["movie"] == 8  # table still there


def test_bad_type_and_sort_are_rejected_or_defaulted(client):
    assert client.get("/v1/letters?type=x").status_code == 400
    assert client.get("/v1/titles?type=movie&letter=P&sort=evil;drop").status_code == 200


def _search(client, **params):
    items = client.get("/v1/search", params={"q": "armageddon", "type": "movie", **params}).json()["items"]
    return [i["id"] for i in items]


def test_search_sort_orders_all_matches(client):
    # votes: Armageddon 480000 > Time 18000 > Alien 1284 > Praying 836; rating: Praying 7.2 > 6.7 > 6.4 > Alien 2.1
    assert _search(client) == ["tt0000011", "tt0000012", "tt0000014", "tt0000013"]  # default = most voted
    assert _search(client, sort="rating") == ["tt0000013", "tt0000011", "tt0000012", "tt0000014"]
    assert _search(client, sort="year") == ["tt0000013", "tt0000012", "tt0000014", "tt0000011"]
    assert _search(client, sort="name") == ["tt0000014", "tt0000011", "tt0000012", "tt0000013"]


def test_search_limit_applies_after_sorting(client):
    # regression: the best-rated hit (Praying for Armageddon, only 836 votes) must survive limit=1 under sort=rating
    assert _search(client, sort="rating", limit=1) == ["tt0000013"]
    assert _search(client, limit=1) == ["tt0000011"]


def test_search_sort_rejects_unknown_value(client):
    assert client.get("/v1/search?q=armageddon&sort=votes;drop").status_code == 400


def test_search_has_more_is_true_only_when_more_than_limit_match(client):
    params = {"q": "armageddon", "type": "movie"}  # 4 matches
    for limit, more, count in ((3, True, 3), (4, False, 4), (5, False, 4)):
        body = client.get("/v1/search", params={**params, "limit": limit}).json()
        assert (body["has_more"], len(body["items"])) == (more, count), limit
    assert client.get("/v1/search?q=zzzzqq").json() == {"has_more": False, "items": []}


def test_titles_has_more_follows_the_page_boundary(client):
    def page(n, per_page):
        return client.get("/v1/titles", params={"type": "movie", "letter": "0-9", "page": n, "per_page": per_page}).json()
    assert client.get("/v1/titles?type=movie&letter=P").json()["has_more"] is False
    assert page(1, 50)["has_more"] is False
    # letter A holds Armageddon, Armageddon Time, Alien Armageddon and Praying lands under P; use per_page=1 on A
    first = client.get("/v1/titles", params={"type": "movie", "letter": "A", "per_page": 1}).json()
    last = client.get("/v1/titles", params={"type": "movie", "letter": "A", "per_page": 1, "page": 3}).json()
    assert (first["has_more"], len(first["items"])) == (True, 1)
    assert (last["has_more"], len(last["items"])) == (False, 1)
    assert last["per_page"] == 1 and last["page"] == 3


def test_title_language_choice_and_fallback(client):
    def first(lang):
        return client.get("/v1/search", params={"q": "kmotr", "type": "movie", **({"lang": lang} if lang else {})}).json()["items"][0]
    assert (first("")["title"], first("cs")["title"]) == ("Kmotr", "Kmotr")
    assert (first("sk")["title"], first("sk")["title_lang"]) == ("Krstný Otec", "sk")
    assert (first("en")["title"], first("en")["title_lang"]) == ("The Godfather", "en")
    assert first("")["titles"] == {"cs": "Kmotr", "sk": "Krstný Otec", "en": "The Godfather"}
    # a title with no Slovak name falls back to Czech instead of an empty card
    pel = client.get("/v1/search", params={"q": "pelisky", "lang": "sk"}).json()["items"][0]
    assert pel["title"] and pel["title_lang"] in ("cs", "sk")


def test_search_finds_a_title_by_any_language_name(client):
    for q in ("godfather", "krstny", "kmotr"):
        assert "tt0000020" in [i["id"] for i in client.get("/v1/search", params={"q": q}).json()["items"]], q


def test_unknown_lang_is_rejected_everywhere(client):
    for url in ("/v1/titles?type=movie&letter=K&lang=de", "/v1/search?q=kmotr&lang=de", "/v1/title/tt0000020?lang=de",
                "/v1/list/series/popular?lang=de", "/v1/title/tt0000003/season/1?lang=de"):
        assert client.get(url).status_code == 400, url
