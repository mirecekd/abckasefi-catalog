# abckasefi-catalog/tests/fake_tmdb.py
"""Canned TMDB answers (no network): `fake_request` replaces app.tmdb_client.request in the tests."""

PAGED_PAGES = 3  # /movie/now_playing: 3 TMDB pages of 20 = 60 items; item n has n votes and TMDB id PAGED_BASE + n
PAGED_BASE = 8000


def paged_imdb(item):
    return f"tt{PAGED_BASE + item:07d}"


CAPPED_BASE = 200000  # ids 200000..209999 = the 500 x 20 items TMDB really serves


def _capped_page(page):
    """Like TMDB: total_pages says 1001 but a page above 500 is refused (request() then returns None)."""
    if page > 500:
        return None
    first = (page - 1) * 20
    return {"results": [{"id": CAPPED_BASE + n, "title": f"Capped {n}", "vote_average": 5.0, "vote_count": n,
                         "release_date": "2020-01-01"} for n in range(first, first + 20)],
            "total_pages": 1001, "total_results": 20020}


def _paged_page(page):
    first = (page - 1) * 20
    items = range(first, first + 20) if page <= PAGED_PAGES else range(0)
    return {"results": [{"id": PAGED_BASE + n, "title": f"Film {n}", "vote_average": 5.0, "vote_count": n,
                         "release_date": "2020-01-01"} for n in items],
            "total_pages": PAGED_PAGES, "total_results": PAGED_PAGES * 20}


def _tv(path):
    if path == "/tv/1396":
        return {"name": "Perníkový táta", "seasons": [
            {"season_number": 1, "name": "Série 1", "episode_count": 2, "air_date": "2008-01-20", "poster_path": "/s1.jpg"},
            {"season_number": 0, "name": "Speciály", "episode_count": 1, "air_date": "", "poster_path": None},
            {"season_number": 2, "name": "Série 2", "episode_count": 1, "air_date": "2009-03-08", "poster_path": None}]}
    if path == "/tv/popular":
        return {"results": [{"id": 501, "name": "Maly hit", "vote_average": 9.0, "vote_count": 1, "first_air_date": "2026-01-01"},
                            {"id": 502, "name": "Solidni", "vote_average": 8.0, "vote_count": 5000, "first_air_date": "2020-01-01"},
                            {"id": 503, "name": "Prumerny", "vote_average": 6.5, "vote_count": 900, "first_air_date": "2024-01-01"}],
                "total_pages": 1}
    if path.startswith("/tv/50") and path.endswith("/external_ids"):
        return {"imdb_id": {"501": "tt0050001", "502": "tt0050002", "503": "tt0050003"}[path.split("/")[2]]}
    return None


def _season(path, params):
    if path == "/tv/1396/season/1" and params.get("language") == "en-US":
        return {"episodes": [{"episode_number": 1, "name": "Pilot EN", "overview": "Pilot synopsis"},
                             {"episode_number": 2, "name": "Cat's in the Bag", "overview": "Bag synopsis"}]}
    if path == "/tv/1396/season/1":
        return {"episodes": [
            {"episode_number": 1, "name": "Pilot", "overview": "Zacatek", "air_date": "2008-01-20", "runtime": 58,
             "still_path": "/e1.jpg", "vote_average": 8.0},
            {"episode_number": 2, "name": "Episode 2", "overview": "", "air_date": "2008-01-27", "runtime": 48,
             "still_path": None, "vote_average": 7.5}]}
    return None


def _movie(path, params):
    if path == "/movie/99" and params.get("language") == "en-US":
        return {"title": "Unknown", "overview": "English synopsis", "tagline": "Eng tag"}
    if path == "/movie/99":
        return {"title": "Neznamy", "original_title": "Unknown", "overview": "", "poster_path": "/n.jpg",
                "release_date": "2020-05-05", "genres": [{"name": "Drama"}], "vote_average": 7.0, "runtime": 100}
    if path == "/movie/77":
        return {"title": "Pelisky", "overview": "Popis filmu", "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
                "vote_average": 8.1, "vote_count": 100, "runtime": 118,
                "credits": {"cast": [{"name": "Jiri Kodet", "character": "Sebesta", "profile_path": "/k.jpg"},
                                      {"name": "Libuse Safrankova", "character": "", "profile_path": None}],
                            "crew": [{"name": "Jan Hrebejk", "job": "Director"},
                                     {"name": "Petr Jarchovsky", "job": "Screenplay"},
                                     {"name": "Petr Jarchovsky", "job": "Writer"},
                                     {"name": "Somebody", "job": "Gaffer"}]}}
    if path == "/movie/top_rated":
        return {"results": [{"id": 77, "title": "Pelisky", "vote_average": 8.1, "release_date": "1999-01-01"},
                            {"id": 99, "title": "Neznamy", "vote_average": 7.0, "release_date": "2020-05-05"}], "total_pages": 3}
    if path == "/movie/now_playing":
        return _paged_page(int(params["page"]))
    if path == "/movie/upcoming":  # the "1001 pages, only 500 served" case
        return _capped_page(int(params["page"]))
    if path.endswith("/external_ids") and path.startswith("/movie/") and path.split("/")[2].isdigit() \
            and CAPPED_BASE <= int(path.split("/")[2]) < CAPPED_BASE + 10000:
        return {"imdb_id": f"tt{int(path.split('/')[2]):07d}"}
    if path == "/movie/77/external_ids":
        return {"imdb_id": "tt0000001"}
    if path == "/movie/99/external_ids":
        return {"imdb_id": "tt9999999"}
    if path.startswith("/movie/80") and path.endswith("/external_ids"):
        return {"imdb_id": f"tt{int(path.split('/')[2]):07d}"}
    return None


def fake_request(path, params):
    if path == "/find/tt9999999":
        return {"movie_results": [{"id": 99}], "tv_results": []}
    if path == "/find/tt0000003":
        return {"movie_results": [], "tv_results": [{"id": 1396}]}
    if path.startswith("/find/"):
        return {"movie_results": [{"id": 77}] if path.endswith("tt0000001") else [], "tv_results": []}
    if path.startswith("/tv/"):
        return _tv(path) or _season(path, params)
    return _movie(path, params)
