# abckasefi-catalog/tests/test_series.py
"""Seasons and episodes: TMDB data and the Cinemeta fallback."""
import httpx
import pytest

from app import config, tmdb_client, tmdb_series

CINEMETA = {"meta": {"videos": [
    {"season": "1", "episode": "1", "name": "Dil 1", "overview": "Popis 1", "released": "2010-05-01T00:00:00.000Z", "thumbnail": "https://img/x.jpg", "rating": "7.5"},
    {"season": "1", "episode": "2", "name": "Dil 2", "overview": "", "released": "2010-05-08T00:00:00.000Z"},
    {"season": "2", "episode": "1", "name": "Dil 3"}, {"season": "x", "episode": "1", "name": "vadny"}]}}


class Resp:
    status_code = 200

    def __init__(self, body=None):
        self.body = {} if body is None else body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture()
def cinemeta(client, monkeypatch):
    monkeypatch.setattr(tmdb_client.http, "get", lambda url, **kw: Resp(CINEMETA))
    return client


def test_series_seasons_sorted_with_specials_last(client):
    r = client.get("/v1/title/tt0000003/seasons").json()
    assert [x["season"] for x in r["seasons"]] == [1, 2, 0]
    assert r["seasons"][0]["poster_url"] == "https://image.tmdb.org/t/p/w185/s1.jpg" and r["seasons"][1]["poster_url"] is None
    assert r["play"] == "plugin://plugin.video.nokturno/?action=seasons&id=tt0000003"


def test_season_episodes_have_nokturno_episode_links(client):
    r = client.get("/v1/title/tt0000003/season/1").json()
    assert [e["episode"] for e in r["episodes"]] == [1, 2]
    assert r["episodes"][0]["still_url"] == "https://image.tmdb.org/t/p/w300/e1.jpg" and r["episodes"][1]["still_url"] is None
    # Nokturno plays an episode as id=<tt>:<S>:<E> with series=<tt> (same shape as its TMDb Helper player)
    assert r["episodes"][1]["play"] == ("plugin://plugin.video.nokturno/?action=play&type=series"
                                        "&id=tt0000003%3A1%3A2&series=tt0000003&ask=1")


def test_seasons_rejects_movies_unknown_and_hostile_ids(client):
    assert client.get("/v1/title/tt0000001/seasons").status_code == 404  # a movie has no seasons
    assert client.get("/v1/title/tt0000003/season/9").status_code == 404  # TMDB has no such season
    assert client.get("/v1/title/tt0000003/season/-1").status_code == 422
    assert client.get("/v1/title/tt0000003/season/999").status_code == 422
    for bad in ("..%2F..%2Fx", "tt1/../../x", "abc", "tt12345678901234"):
        assert client.get(f"/v1/title/{bad}/seasons").status_code == 404, bad
    assert client.get("/v1/title/..%2Fsecret").status_code == 404


def test_seasons_without_tmdb_key_is_400(client, monkeypatch):
    monkeypatch.setattr(config, "TMDB_KEY", "")
    assert client.get("/v1/title/tt0000003/seasons").status_code == 400


def test_episode_overview_and_placeholder_title_fall_back_to_english(client):
    eps = client.get("/v1/title/tt0000003/season/1").json()["episodes"]
    assert (eps[0]["name"], eps[0]["overview"], eps[0]["overview_lang"]) == ("Pilot", "Zacatek", "cs")
    # Czech overview is empty and the title is TMDB's "Episode 2" placeholder -> English text and name
    assert (eps[1]["name"], eps[1]["overview"], eps[1]["overview_lang"]) == ("Cat's in the Bag", "Bag synopsis", "en")


def test_series_unknown_to_tmdb_get_seasons_and_episodes_from_cinemeta(cinemeta):
    # regression: "A Cup of Style" (known to Cinemeta, not to TMDB) answered 404 "Serie se nepodarilo nacist"
    seasons = cinemeta.get("/v1/title/tt7215640/seasons").json()["seasons"]
    assert [(x["season"], x["episodes"], x["source"]) for x in seasons] == [(1, 2, "cinemeta"), (2, 1, "cinemeta")]
    eps = cinemeta.get("/v1/title/tt7215640/season/1").json()["episodes"]
    assert [(e["episode"], e["name"], e["air_date"]) for e in eps] == [(1, "Dil 1", "2010-05-01"), (2, "Dil 2", "2010-05-08")]
    assert eps[0]["still_url"] == "https://img/x.jpg" and eps[0]["overview_lang"] == "en" and eps[1]["overview_lang"] == ""
    assert eps[0]["play"].endswith("id=tt7215640%3A1%3A1&series=tt7215640&ask=1")


def test_known_series_without_episodes_anywhere_says_so_instead_of_404(client, monkeypatch):
    # regression: "A Cup of Style" (a catalog series, in neither TMDB nor Cinemeta) showed a bare "Chyba 404"
    monkeypatch.setattr(tmdb_client.http, "get", lambda url, **kw: Resp())
    r = client.get("/v1/title/tt0000007/seasons")  # tt0000007 is a series in the synthetic catalog
    assert r.status_code == 200 and r.json()["seasons"] == [] and "Nokturno" in r.json()["note"]
    assert client.get("/v1/title/tt7777777/seasons").status_code == 404  # not in the catalog at all
    assert client.get("/v1/title/tt0000001/seasons").status_code == 404  # a movie


def test_cinemeta_empty_answer_is_cached_as_nothing_and_redirects_are_followed(client, monkeypatch, caplog):
    # regression: Cinemeta answers 307 -> cinemeta-live.strem.io -> `{}` for series it does not know; without
    # following the redirect every click logged "HTTPStatusError" and re-queried the server
    calls = []

    def fake_get(url, **kw):
        calls.append(kw.get("follow_redirects"))
        return Resp()

    monkeypatch.setattr(tmdb_client.http, "get", fake_get)
    with caplog.at_level("WARNING", logger="catalog.tmdb"):
        for _ in range(3):
            assert tmdb_series._cinemeta("tt0000777") is None
    assert calls == [True] and not caplog.records  # one request (redirects on), then served from cache


def test_cinemeta_404_is_also_cached_as_nothing(client, monkeypatch):
    calls = []

    class NotFound:
        status_code = 404

        def raise_for_status(self):
            raise AssertionError("404 must be handled before raise_for_status")

    monkeypatch.setattr(tmdb_client.http, "get", lambda url, **kw: calls.append(url) or NotFound())
    assert tmdb_series._cinemeta("tt0000779") is None and tmdb_series._cinemeta("tt0000779") is None
    assert len(calls) == 1


def test_cinemeta_network_error_is_not_cached(client, monkeypatch):
    calls = []

    def boom(url, **kw):
        calls.append(url)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(tmdb_client.http, "get", boom)
    assert tmdb_series._cinemeta("tt0000778") is None and tmdb_series._cinemeta("tt0000778") is None
    assert len(calls) == 2  # a transient failure must be retried
