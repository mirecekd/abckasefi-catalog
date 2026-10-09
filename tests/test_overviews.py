# abckasefi-catalog/tests/test_overviews.py
"""Plot summaries in list responses: cache first, no TMDB call in the request path, background fill-in without duplicates."""
import threading
import time

import pytest

from app import config, db, overviews, tmdb_client
from tests.fake_tmdb import fake_request


@pytest.fixture()
def tmdb_calls(monkeypatch, client):
    """Record every TMDB request with the name of the thread that made it (fake answers, no network)."""
    calls = []

    def recording(path, params):
        calls.append((path, threading.current_thread().name))
        return fake_request(path, params)

    monkeypatch.setattr(tmdb_client, "request", recording)
    overviews.PENDING.clear()
    overviews._FAILED.clear()
    return calls


def _wait_idle():
    deadline = time.time() + 10
    while overviews.PENDING and time.time() < deadline:
        time.sleep(0.01)
    assert not overviews.PENDING


def _overviews(body):
    return {i["id"]: (i["overview"], i["overview_lang"]) for i in body["items"]}


def _clear_cache(*ids):
    conn = db.connect(db.CACHE)
    try:
        for imdb in ids:
            conn.execute("DELETE FROM kv WHERE key LIKE ?", (f"%{imdb}%",))
        conn.commit()
    finally:
        conn.close()


def test_list_item_has_overview_fields_even_without_any_source(client, tmdb_calls):
    _clear_cache("tt0000030", "tt0000031", "tt0000032")
    body = client.get("/v1/titles?type=movie&letter=Q").json()
    assert all(i["overview"] == "" and i["overview_lang"] == "" for i in body["items"])
    _wait_idle()
    assert all(thread.startswith("overview") for _, thread in tmdb_calls)  # only the background pool talked to TMDB


def test_overview_from_cache_without_tmdb_call_in_request_path(client, tmdb_calls):
    db.kv_set(f"overview{overviews.tmdb_detail.DETAIL_VERSION}:cs:tt0000031",
              {"overview": "Cached plot", "tagline": "", "overview_lang": "cs"})
    request_thread = threading.current_thread().name
    body = client.get("/v1/titles?type=movie&letter=Q").json()
    assert _overviews(body)["tt0000031"] == ("Cached plot", "cs")
    _wait_idle()
    assert all(thread != request_thread for _, thread in tmdb_calls)


def test_background_fill_in_makes_the_plot_visible_on_the_next_view(client, tmdb_calls):
    _clear_cache("tt0000001")
    first = client.get("/v1/search?q=pelisk").json()
    assert _overviews(first)["tt0000001"] == ("", "")  # nothing cached yet, nothing fetched in the request
    _wait_idle()
    assert any(path == "/movie/77" for path, _ in tmdb_calls)
    assert all(thread.startswith("overview") for _, thread in tmdb_calls)
    assert _overviews(client.get("/v1/search?q=pelisk").json())["tt0000001"] == ("Popis filmu", "cs")


def test_nothing_is_fetched_twice(client, tmdb_calls):
    _clear_cache("tt0000001")
    for _ in range(3):
        client.get("/v1/search?q=pelisk")
    _wait_idle()
    client.get("/v1/search?q=pelisk")
    _wait_idle()
    assert len([p for p, _ in tmdb_calls if p == "/movie/77"]) == 2  # detail (credits) + overview, once each


def test_schedule_skips_ids_already_pending(client, tmdb_calls):
    _clear_cache("tt0000001")
    gate = threading.Event()
    started = []

    def blocked(*args):
        started.append(args)
        gate.wait(5)

    original = overviews._fetch
    overviews._fetch = blocked
    try:
        overviews.schedule([("tt0000001", "movie")])
        overviews.schedule([("tt0000001", "movie")])
        overviews.schedule([("tt0000001", "movie")], "en")  # another language is another key
        deadline = time.time() + 5
        while len(started) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert len(started) == 2 and len(overviews.PENDING) == 2
    finally:
        gate.set()
        overviews._fetch = original
        overviews.PENDING.clear()


def test_at_most_forty_titles_per_request_and_three_workers(client, tmdb_calls, monkeypatch):
    queued = []
    monkeypatch.setattr(overviews.POOL, "submit", lambda fn, *a: queued.append(a))
    overviews.PENDING.clear()
    overviews.schedule([(f"tt{n:07d}", "movie") for n in range(100)])
    assert len(queued) == overviews.MAX_PER_REQUEST == 40
    overviews.PENDING.clear()
    assert overviews.WORKERS == 3 and overviews.POOL._max_workers == 3


def test_no_tmdb_key_means_no_background_work(client, tmdb_calls, monkeypatch):
    monkeypatch.setattr(config, "TMDB_KEY", "")
    _clear_cache("tt0000001")
    client.get("/v1/search?q=pelisk")
    time.sleep(0.1)
    assert not overviews.PENDING and tmdb_calls == []


def test_tmdb_outage_does_not_break_anything_and_failures_are_not_retried_at_once(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tmdb_client, "request", lambda path, params: calls.append(path))
    overviews.PENDING.clear()
    overviews._FAILED.clear()
    _clear_cache("tt0000001")
    assert client.get("/v1/search?q=pelisk").status_code == 200
    _wait_idle()
    n = len(calls)
    assert n >= 1 and ("tt0000001", "cs") in overviews._FAILED
    client.get("/v1/search?q=pelisk")
    _wait_idle()
    assert len(calls) == n


def test_tmdb_list_takes_overview_from_the_result_and_prefers_the_cache(client, tmdb_calls, monkeypatch):
    def with_overview(path, params):
        if path == "/movie/top_rated":
            return {"results": [{"id": 77, "title": "Pelisky", "overview": "From the list"},
                                {"id": 99, "title": "Neznamy", "overview": "Unknown from the list"}], "total_pages": 1}
        return fake_request(path, params)

    monkeypatch.setattr(tmdb_client, "request", with_overview)
    _clear_cache("tt0000001", "tt9999999", "list:movie:top_rated:1")
    items = client.get("/v1/list/movie/top_rated?lang=en").json()["items"]
    assert [(i["overview"], i["overview_lang"]) for i in items] == [("From the list", "cs"), ("Unknown from the list", "cs")]
    _wait_idle()
    db.kv_set(f"overview{overviews.tmdb_detail.DETAIL_VERSION}:en:tt0000001",
              {"overview": "Cached EN", "tagline": "", "overview_lang": "en"})
    again = _overviews(client.get("/v1/list/movie/top_rated?lang=en").json())
    assert again["tt0000001"] == ("Cached EN", "en")
