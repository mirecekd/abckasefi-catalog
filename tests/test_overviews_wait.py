# abckasefi-catalog/tests/test_overviews_wait.py
"""The bounded wait for plots inside a list request (OVERVIEW_WAIT_S) and the config values behind it."""
import threading
import time

import pytest

from app import config, overviews, tmdb_client
from tests.test_overviews import _clear_cache, _overviews, _wait_idle, tmdb_calls  # noqa: F401 - tmdb_calls is a fixture

Q_IDS = ("tt0000030", "tt0000031", "tt0000032")  # the three films of /v1/titles?type=movie&letter=Q
Q_URL = "/v1/titles?type=movie&letter=Q"


def _plot(imdb):
    return f"Plot of {imdb}"


def _install_fake(monkeypatch, calls, slow=None, started=None):
    """TMDB stand-in that knows every Q title (TMDB id 9000 + number). `slow` maps an imdb id to the seconds its /find
    call takes; `started` is set on the first /find call. Every call is recorded as (path, thread name)."""
    def fake(path, params):
        calls.append((path, threading.current_thread().name))
        if path.startswith("/find/"):
            imdb = path[len("/find/"):]
            if started is not None:
                started.set()
            time.sleep((slow or {}).get(imdb, 0))
            return {"movie_results": [{"id": 9000 + int(imdb[2:])}], "tv_results": []}
        number = int(path.split("/")[2])
        return {"title": f"Film {number}", "overview": _plot(f"tt{number - 9000:07d}"), "poster_path": None}

    monkeypatch.setattr(tmdb_client, "request", fake)
    overviews.PENDING.clear()
    overviews._FAILED.clear()
    _clear_cache(*Q_IDS)


def test_first_view_has_all_plots_when_the_fetches_fit_the_budget(client, monkeypatch):
    calls = []
    _install_fake(monkeypatch, calls)
    request_thread = threading.current_thread().name
    body = client.get(Q_URL).json()
    assert _overviews(body) == {imdb: (_plot(imdb), "cs") for imdb in Q_IDS}
    _wait_idle()
    assert calls and all(thread.startswith("overview") and thread != request_thread for _, thread in calls)


def test_slow_fetches_do_not_hold_the_response_beyond_the_budget(client, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "OVERVIEW_WAIT_S", 0.3)
    _install_fake(monkeypatch, calls, slow={"tt0000031": 1.2})
    started = time.monotonic()
    first = _overviews(client.get(Q_URL).json())
    elapsed = time.monotonic() - started
    assert elapsed < 0.3 + 0.7
    assert first["tt0000030"] == (_plot("tt0000030"), "cs") and first["tt0000032"] == (_plot("tt0000032"), "cs")
    assert first["tt0000031"] == ("", "")
    assert ("tt0000031", "cs") in overviews.PENDING  # not cancelled, still running
    _wait_idle()
    assert _overviews(client.get(Q_URL).json())["tt0000031"] == (_plot("tt0000031"), "cs")


def test_zero_budget_keeps_the_old_background_behaviour(client, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "OVERVIEW_WAIT_S", 0.0)
    _install_fake(monkeypatch, calls)
    assert set(_overviews(client.get(Q_URL).json()).values()) == {("", "")}
    _wait_idle()
    assert set(_overviews(client.get(Q_URL).json()).values()) == {(_plot(i), "cs") for i in Q_IDS}


def test_concurrent_requests_fetch_each_title_once(client, monkeypatch):
    calls = []
    started = threading.Event()
    monkeypatch.setattr(config, "OVERVIEW_WAIT_S", 5.0)
    _install_fake(monkeypatch, calls, slow=dict.fromkeys(Q_IDS, 0.5), started=started)
    bodies = []
    first = threading.Thread(target=lambda: bodies.append(client.get(Q_URL).json()))
    first.start()
    assert started.wait(5)  # the first request has queued its fetches, the second one must join them
    bodies.append(client.get(Q_URL).json())
    first.join(10)
    assert len(bodies) == 2
    for body in bodies:
        assert _overviews(body) == {imdb: (_plot(imdb), "cs") for imdb in Q_IDS}
    _wait_idle()
    assert sorted(path for path, _ in calls) == sorted([f"/find/{i}" for i in Q_IDS] + [f"/movie/{9000 + int(i[2:])}" for i in Q_IDS] * 2)


def test_failed_fetch_gives_empty_plot_and_is_not_retried_within_the_retry_window(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tmdb_client, "request", lambda path, params: calls.append(path))
    overviews.PENDING.clear()
    overviews._FAILED.clear()
    _clear_cache(*Q_IDS)
    response = client.get(Q_URL)
    assert response.status_code == 200
    assert set(_overviews(response.json()).values()) == {("", "")}
    assert not overviews.PENDING and {(i, "cs") for i in Q_IDS} <= set(overviews._FAILED)
    asked = len(calls)
    assert asked == len(Q_IDS)  # one /find per title
    assert client.get(Q_URL).status_code == 200
    assert len(calls) == asked


@pytest.mark.parametrize("raw, expected", [("1.5", 1.5), ("0", 0.0), ("-3", 0.0), ("99", 8.0), ("abc", 2.5), ("nan", 2.5),
                                           ("inf", 2.5), ("", 2.5)])
def test_overview_wait_is_a_float_clamped_to_zero_to_eight(monkeypatch, raw, expected):
    monkeypatch.setenv("OVERVIEW_WAIT_S", raw)
    assert config._clamped_float("OVERVIEW_WAIT_S", 2.5, 0.0, 8.0) == expected


@pytest.mark.parametrize("raw, expected", [("4", 4), ("0", 1), ("-2", 1), ("99", 16), ("2.5", 8), ("abc", 8)])
def test_overview_workers_is_an_int_clamped_to_one_to_sixteen(monkeypatch, raw, expected):
    monkeypatch.setenv("OVERVIEW_WORKERS", raw)
    assert config._clamped_int("OVERVIEW_WORKERS", 8, 1, 16) == expected


def test_defaults():
    assert config.OVERVIEW_WAIT_S == 2.5 and config.OVERVIEW_WORKERS == 8
    assert overviews.MAX_PER_REQUEST == 60
