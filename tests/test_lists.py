# abckasefi-catalog/tests/test_lists.py
"""TMDB list endpoint: IMDb mapping, sorting and per_page pagination."""
import contextlib
import threading

from app import db, routes_lists, tmdb_client
from tests.fake_tmdb import PAGED_PAGES, paged_imdb


def test_tmdb_list_maps_to_imdb_and_keeps_titles_not_in_catalog(client):
    r = client.get("/v1/list/movie/top_rated").json()
    assert [i["id"] for i in r["items"]] == ["tt0000001", "tt9999999"]
    assert r["items"][0]["title"] == "Pelíšky" and r["items"][1]["title"] == "Neznamy"
    assert client.get("/v1/list/movie/nope").status_code in (400, 503)


def _list_ids(client, **params):
    return [i["id"] for i in client.get("/v1/list/series/popular", params=params).json()["items"]]


def test_list_sort_rating_ranks_reliable_ratings_first(client):
    # regression: "trend today by rating" put 9.0 from 1-2 votes above 8.4 from 160 000 votes
    assert _list_ids(client) == ["tt0050001", "tt0050002", "tt0050003"]  # TMDB order untouched by default
    assert _list_ids(client, sort="rating") == ["tt0050002", "tt0050003", "tt0050001"]
    assert _list_ids(client, sort="votes") == ["tt0050002", "tt0050003", "tt0050001"]
    assert _list_ids(client, sort="year") == ["tt0050001", "tt0050003", "tt0050002"]
    # names: Maly hit, Solidni, Prumerny -> alphabetical = Maly hit, Prumerny, Solidni
    assert _list_ids(client, sort="name") == ["tt0050001", "tt0050003", "tt0050002"]
    assert client.get("/v1/list/series/popular?sort=bogus").status_code == 400


def test_list_items_flag_unreliable_rating_and_say_sort_scope(client):
    r = client.get("/v1/list/series/popular?sort=rating").json()
    flags = {i["id"]: i["rating_reliable"] for i in r["items"]}
    assert flags == {"tt0050001": False, "tt0050002": True, "tt0050003": True}
    assert r["sorted_within_page"] is True
    assert client.get("/v1/list/series/popular").json()["sorted_within_page"] is False


def _paged(client, **params):
    return client.get("/v1/list/movie/now_playing", params=params).json()


def _ids(body):
    return [i["id"] for i in body["items"]]


def test_list_default_page_is_twenty_items_and_reports_paging(client):
    body = _paged(client)
    assert _ids(body) == [paged_imdb(n) for n in range(20)]
    assert (body["page"], body["per_page"], body["total_pages"], body["has_more"]) == (1, 20, PAGED_PAGES, True)
    assert _paged(client, page=3)["has_more"] is False


def test_list_per_page_spanning_tmdb_pages_uses_the_right_global_slice(client):
    body = _paged(client, per_page=50, page=1)  # items 0..49 = TMDB pages 1..3
    assert _ids(body) == [paged_imdb(n) for n in range(50)]
    assert (body["total_pages"], body["has_more"]) == (2, True)
    tail = _paged(client, per_page=50, page=2)  # items 50..59 only
    assert _ids(tail) == [paged_imdb(n) for n in range(50, 60)]
    assert tail["has_more"] is False
    mid = _paged(client, per_page=15, page=2)  # items 15..29: crosses the TMDB page boundary at 20
    assert _ids(mid) == [paged_imdb(n) for n in range(15, 30)]
    assert (mid["total_pages"], mid["per_page"]) == (4, 15)
    whole = _paged(client, per_page=100)
    assert (whole["total_pages"], whole["has_more"], len(whole["items"])) == (1, False, 60)


def test_list_page_beyond_the_end_is_empty_without_more(client):
    body = _paged(client, per_page=50, page=3)
    assert (body["items"], body["has_more"]) == ([], False)


def _forget_cached_list_pages():
    conn = db.connect(db.CACHE)
    try:
        conn.execute("DELETE FROM kv WHERE key LIKE 'list:movie:now_playing:%'")
        conn.commit()
    finally:
        conn.close()


def test_list_per_page_fetches_only_the_needed_tmdb_pages(client, monkeypatch):
    seen = []
    orig = tmdb_client.request

    def spy(path, params):
        if path == "/movie/now_playing":
            seen.append(params["page"])
        return orig(path, params)

    monkeypatch.setattr(tmdb_client, "request", spy)
    _forget_cached_list_pages()
    _paged(client, per_page=10, page=3)  # items 20..29 -> TMDB page 2 only
    assert seen == [2]
    _paged(client, per_page=25, page=1)  # items 0..24 -> TMDB pages 1 and 2; page 2 is cached already
    assert seen == [2, 1]
    _paged(client, per_page=25, page=1)
    assert seen == [2, 1]  # everything cached now


def test_list_resolves_imdb_ids_concurrently_with_at_most_eight_workers(client, monkeypatch):
    active, peak, lock = [0], [0], threading.Lock()
    gate = threading.Barrier(2, timeout=0.2)

    def slow_imdb_of(media, tmdb_id):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        with contextlib.suppress(threading.BrokenBarrierError):
            gate.wait()
        with lock:
            active[0] -= 1
        return f"tt{tmdb_id:07d}"

    monkeypatch.setattr(tmdb_client, "imdb_of", slow_imdb_of)
    body = _paged(client, per_page=40, page=1)
    assert len(body["items"]) == 40
    assert 2 <= peak[0] <= routes_lists.IMDB_WORKERS == 8


def test_list_per_page_is_validated(client):
    for bad in (0, 101, -1):
        assert client.get("/v1/list/movie/now_playing", params={"per_page": bad}).status_code == 422, bad
    assert client.get("/v1/list/movie/now_playing", params={"per_page": 100}).status_code == 200


def test_list_sort_applies_within_the_returned_page(client):
    body = _paged(client, per_page=10, page=2, sort="votes")  # items 10..19 -> highest votes first
    assert _ids(body) == [paged_imdb(n) for n in range(19, 9, -1)]
    assert body["sorted_within_page"] is True and body["has_more"] is True


def test_list_unavailable_tmdb_is_503(client, monkeypatch):
    monkeypatch.setattr(tmdb_client, "request", lambda path, params: None)
    conn = db.connect(db.CACHE)
    try:
        conn.execute("DELETE FROM kv WHERE key LIKE 'list:movie:popular:%'")
        conn.commit()
    finally:
        conn.close()
    assert client.get("/v1/list/movie/popular", params={"per_page": 50}).status_code == 503


def _capped(client, **params):
    return client.get("/v1/list/movie/upcoming", params=params)


def test_list_is_clamped_to_the_500_pages_tmdb_actually_serves(client):
    # regression: total_pages said 1001, the last real page 100 (per_page=100) answered 422/5xx instead of data
    last = _capped(client, per_page=100, page=100)
    assert last.status_code == 200
    body = last.json()
    assert (body["has_more"], body["total_pages"], len(body["items"])) == (False, 100, 100)
    assert body["items"][-1]["id"] == "tt0209999"  # item 9999 = the last of the 500 x 20


def test_list_page_beyond_the_tmdb_cap_is_empty_not_an_error(client):
    body = _capped(client, per_page=100, page=101).json()
    assert (body["items"], body["has_more"]) == ([], False)
    assert _capped(client, per_page=20, page=501).status_code == 422  # page itself is bounded to 500
    assert _capped(client, per_page=20, page=500).json()["has_more"] is False


def test_list_pages_below_the_cap_still_report_more(client):
    assert _capped(client, per_page=100, page=99).json()["has_more"] is True
