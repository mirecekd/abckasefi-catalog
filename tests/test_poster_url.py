# abckasefi-catalog/tests/test_poster_url.py
"""poster_url: the direct TMDB image URL (w342) of every list item, from caches only, "" when unknown."""
import threading

from app import config, db, overviews, tmdb_client
from tests.fake_tmdb import fake_request
from tests.test_overviews import _clear_cache, _wait_idle

Q_URL = "/v1/titles?type=movie&letter=Q"
W342 = "https://image.tmdb.org/t/p/w342"


def _posters(body):
    return {i["id"]: i["poster_url"] for i in body["items"]}


def _cache_detail(imdb, poster):
    db.kv_set(f"detail{overviews.tmdb_detail.DETAIL_VERSION}:{imdb}", {"media": "movie", "tmdb_id": 1, "poster": poster})


def test_poster_url_comes_from_the_cached_detail_without_tmdb_call_in_request_path(client, tmdb_calls):
    _clear_cache("tt0000030", "tt0000031", "tt0000032")
    _cache_detail("tt0000031", "/abc.jpg")
    request_thread = threading.current_thread().name
    body = client.get(Q_URL).json()
    assert _posters(body)["tt0000031"] == f"{W342}/abc.jpg" == tmdb_client.image_url("/abc.jpg", "w342")
    assert _posters(body)["tt0000030"] == ""
    _wait_idle()
    assert all(thread != request_thread for _, thread in tmdb_calls)


def test_poster_url_is_empty_without_any_cache(client, monkeypatch):
    monkeypatch.setattr(config, "TMDB_KEY", "")
    _clear_cache("tt0000030", "tt0000031", "tt0000032")
    assert set(_posters(client.get(Q_URL).json()).values()) == {""}


def test_poster_url_is_empty_when_the_cached_detail_has_no_poster(client, tmdb_calls):
    _clear_cache("tt0000031")
    _cache_detail("tt0000031", None)
    assert _posters(client.get(Q_URL).json())["tt0000031"] == ""
    _wait_idle()


def test_every_list_item_has_the_field_even_without_a_tmdb_key(client, monkeypatch):
    monkeypatch.setattr(config, "TMDB_KEY", "")
    for url in (Q_URL, "/v1/search?q=pelisk"):
        items = client.get(url).json()["items"]
        assert items and all(isinstance(i["poster_url"], str) and i["poster"].startswith("/v1/poster/") for i in items)


def test_tmdb_list_items_take_poster_url_from_poster_path(client, tmdb_calls, monkeypatch):
    def with_posters(path, params):
        if path == "/movie/top_rated":
            return {"results": [{"id": 77, "title": "Pelisky", "poster_path": "/list77.jpg"},
                                {"id": 99, "title": "Neznamy", "poster_path": "/list99.jpg"}], "total_pages": 1}
        return fake_request(path, params)

    monkeypatch.setattr(tmdb_client, "request", with_posters)
    _clear_cache("tt0000001", "tt9999999", "list:movie:top_rated:1")
    items = client.get("/v1/list/movie/top_rated").json()["items"]
    assert [i["poster_url"] for i in items] == [f"{W342}/list77.jpg", f"{W342}/list99.jpg"]
    _wait_idle()


def test_poster_url_of_freshly_fetched_items_is_in_the_same_response_after_the_wait(client, tmdb_calls, monkeypatch):
    monkeypatch.setattr(config, "OVERVIEW_WAIT_S", 5.0)
    _clear_cache("tt0000001")
    request_thread = threading.current_thread().name
    body = client.get("/v1/search?q=pelisk").json()
    assert _posters(body)["tt0000001"] == f"{W342}/p.jpg"
    _wait_idle()
    assert tmdb_calls and all(thread != request_thread for _, thread in tmdb_calls)
