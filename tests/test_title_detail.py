# abckasefi-catalog/tests/test_title_detail.py
"""Title detail, poster, auth/ping and the language of overviews."""
from app import config, db, tmdb_client, tmdb_detail


def test_detail_adds_czech_overview_and_nokturno_links(client):
    d = client.get("/v1/title/tt0000001").json()
    assert d["overview"] == "Popis filmu" and d["poster_url"].startswith("https://image.tmdb.org/t/p/w500/")
    assert d["play"] == "plugin://plugin.video.nokturno/?action=play&type=movie&id=tt0000001&ask=1"
    s = client.get("/v1/title/tt0000003").json()
    assert s["play"] == "plugin://plugin.video.nokturno/?action=seasons&id=tt0000003"
    assert client.get("/v1/title/ttnope").status_code == 404


def test_poster_is_lazy_redirect_and_404_when_missing(client):
    r = client.get("/v1/poster/tt0000001?size=w185", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://image.tmdb.org/t/p/w185/p.jpg"
    assert client.get("/v1/poster/tt0000003", follow_redirects=False).status_code == 404
    assert client.get("/v1/poster/tt0000001?size=w9999").status_code == 400


def test_token_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "s3cret")
    assert client.get("/v1/letters?type=movie").status_code == 401
    assert client.get("/v1/letters?type=movie", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/v1/letters?type=movie&token=s3cret").status_code == 200
    assert client.get("/v1/poster/tt0000001", follow_redirects=False).status_code == 401  # protects the TMDB quota
    assert client.get("/v1/poster/tt0000001?token=s3cret", follow_redirects=False).status_code == 302
    assert client.get("/health").status_code == 200  # health is public


def test_ping_reports_version_and_state_without_a_token_configured(client):
    r = client.get("/v1/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": "0.1.0", "tmdb": True, "ready": True}


def test_ping_requires_the_token_when_configured(client, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "s3cret")
    assert client.get("/v1/ping").status_code == 401
    assert client.get("/v1/ping", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/ping", headers={"Authorization": "Bearer s3cret"}).json()["ok"] is True
    assert client.get("/v1/ping?token=s3cret").status_code == 200
    assert client.get("/health").status_code == 200  # still public


def test_ping_reflects_missing_tmdb_key_and_unbuilt_catalog(client, monkeypatch):
    monkeypatch.setattr(config, "TMDB_KEY", "")
    monkeypatch.setattr(db, "ready", lambda: False)
    body = client.get("/v1/ping").json()
    assert (body["ok"], body["tmdb"], body["ready"]) == (True, False, False)


def test_detail_during_first_build_is_503_not_404(client, monkeypatch):
    # regression: clicking a card while catalog.db did not exist yet answered 404 "unknown id"
    monkeypatch.setattr(db, "ready", lambda: False)
    assert client.get("/v1/title/tt0000001").status_code == 503
    assert client.get("/v1/letters?type=movie").status_code == 503


def test_detail_of_title_missing_from_catalog_falls_back_to_tmdb(client):
    # regression: cards from TMDB lists (new/rare titles not in the catalog) answered 404 when clicked
    d = client.get("/v1/title/tt9999999")
    assert d.status_code == 200
    body = d.json()
    assert (body["title"], body["year"], body["type"], body["genres"]) == ("Neznamy", 2020, "movie", ["Drama"])
    assert body["play"].endswith("action=play&type=movie&id=tt9999999&ask=1")
    assert client.get("/v1/title/ttnotintmdb").status_code == 404  # truly unknown stays 404


def test_old_detail_cache_entries_are_ignored(client):
    # regression: a detail cached before new fields were added made missing-from-catalog titles 404 forever
    db.kv_set("detail:tt9999999", {"title": "Neskutecna", "overview": "", "poster": "/x.jpg"})
    body = client.get("/v1/title/tt9999999").json()
    assert body["year"] == 2020 and body["genres"] == ["Drama"]


def test_missing_czech_overview_falls_back_to_english_and_says_so(client):
    body = client.get("/v1/title/tt9999999").json()
    assert (body["overview"], body["overview_lang"]) == ("English synopsis", "en")
    cz = client.get("/v1/title/tt0000001").json()
    assert (cz["overview"], cz["overview_lang"]) == ("Popis filmu", "cs")


def test_overview_is_requested_and_cached_per_language(client, monkeypatch):
    # TMDB: Czech and English texts exist, Slovak is empty
    texts = {"cs-CZ": "Cesky popis", "sk-SK": "", "en-US": "English text"}
    seen = []
    orig = tmdb_client.request

    def spy(path, params):
        if path == "/movie/77":
            seen.append(params.get("language"))
            return {"overview": texts.get(params.get("language") or "cs-CZ", ""), "poster_path": "/p.jpg", "title": "T"}
        return orig(path, params)

    monkeypatch.setattr(tmdb_client, "request", spy)
    # the cache is shared across tests, so use an id nobody else touched
    monkeypatch.setattr(tmdb_client, "find", lambda imdb_id: {"tmdb_id": 77, "media": "movie"})
    cs = client.get("/v1/title/tt0000008?lang=cs").json()
    sk = client.get("/v1/title/tt0000008?lang=sk").json()
    en = client.get("/v1/title/tt0000008?lang=en").json()
    assert (cs["overview"], cs["overview_lang"]) == ("Cesky popis", "cs")
    assert (sk["overview"], sk["overview_lang"]) == ("English text", "en")  # Slovak missing -> English, and says so
    assert (en["overview"], en["overview_lang"]) == ("English text", "en")
    calls = len(seen)
    for lang in ("cs", "sk", "en"):
        client.get(f"/v1/title/tt0000008?lang={lang}")
    assert len(seen) == calls  # a second visit in the same languages is served entirely from the cache
    assert tmdb_detail.DETAIL_VERSION >= 4
