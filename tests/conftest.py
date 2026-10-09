# abckasefi-catalog/tests/conftest.py
"""Test fixtures: a tiny synthetic IMDb dataset and an isolated DATA_DIR per test run."""
import gzip
import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from .fake_tmdb import fake_request

DATA = tempfile.mkdtemp(prefix="catalog-test-")
os.environ["DATA_DIR"] = DATA
os.environ["TMDB_API_KEY"] = "testkey"
os.environ["MIN_VOTES"] = "100"
os.environ["LOCAL_MIN_VOTES"] = "5"

BASICS = [
    ("tt0000020", "movie", "The Godfather", "0", "1972", "Crime,Drama"),
    ("tt0000001", "movie", "Pelisky", "0", "1999", "Comedy,Drama"),
    ("tt0000002", "movie", "Cesky film bez hlasu", "0", "2001", "Drama"),
    ("tt0000003", "tvSeries", "Breaking Bad", "0", "2008", "Crime,Drama"),
    ("tt0000004", "movie", "Zlaty film", "1", "2005", "Adult"),
    ("tt0000005", "movie", "7 dni", "0", "2010", "Drama"),
    ("tt0000006", "movie", "Ostrov", "0", "2012", "Drama"),
    ("tt0000007", "tvSeries", "Zakon", "0", "2015", "Drama"),
    ("tt0000011", "movie", "Armageddon", "0", "1998", "Action"),
    ("tt0000012", "movie", "Armageddon Time", "0", "2022", "Drama"),
    ("tt0000013", "movie", "Praying for Armageddon", "0", "2023", "Doc"),
    ("tt0000014", "movie", "Alien Armageddon", "0", "2011", "Sci-Fi"),
    ("tt0000030", "movie", "Quorum Obscure", "0", "2003", "Drama"),
    ("tt0000031", "movie", "Quasar Epic", "0", "1990", "Sci-Fi"),
    ("tt0000032", "movie", "Quartz Middle", "0", "2018", "Drama"),
]
RATINGS = {"tt0000020": (9.2, 2000000),"tt0000001": (8.2, 5000), "tt0000002": (6.0, 7), "tt0000003": (9.5, 90000), "tt0000004": (5.0, 9000),
           "tt0000005": (7.0, 300), "tt0000006": (6.5, 50), "tt0000007": (7.7, 800),
           "tt0000011": (6.7, 480000), "tt0000012": (6.4, 18000), "tt0000013": (7.2, 836), "tt0000014": (2.1, 1284),
           "tt0000030": (9.4, 20), "tt0000031": (8.8, 2000000), "tt0000032": (7.0, 5000)}  # letter Q: sorting fixtures
AKAS = [("tt0000001", "CZ", "Pelíšky"), ("tt0000001", "CZ", "Pracovni nazev:working"), ("tt0000002", "CZ", "Český film"), ("tt0000003", "CZ", "Perníkový táta"),
        ("tt0000005", "SK", "7 dní"), ("tt0000030", "CZ", "Quorum Obscure"), ("tt0000007", "CZ", "Čelisti: Zákon")]


def _write(name, header, rows):
    path = os.path.join(DATA, "imdb")
    os.makedirs(path, exist_ok=True)
    with gzip.open(os.path.join(path, name + ".tsv.gz"), "wt", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")


@pytest.fixture(scope="session", autouse=True)
def built():
    from app import db, importer
    _write("title.basics", ["tconst", "titleType", "primaryTitle", "originalTitle", "isAdult", "startYear", "endYear", "runtimeMinutes", "genres"],
           [(t, ty, n, n, ad, y, "\\N", "90", g) for t, ty, n, ad, y, g in BASICS])
    _write("title.ratings", ["tconst", "averageRating", "numVotes"], [(t, str(r), str(v)) for t, (r, v) in RATINGS.items()])
    _write("title.akas", ["titleId", "ordering", "title", "region", "language", "types", "attributes", "isOriginalTitle"],
           [(t, "1", n.split(":")[0], reg, "\\N", "working" if ":working" in n else "imdbDisplay", "\\N", "0") for t, reg, n in AKAS]
           + [(t, "1", n, reg, lang, "imdbDisplay", "\\N", "0") for t, reg, lang, n in GODFATHER_AKAS])
    db.init()
    return importer.build()


# --- language-aware rows (the Godfather case): Czech only via language 'cs' with the old region CSHH ---
GODFATHER = ("tt0000020", "movie", "The Godfather", "0", "1972", "Crime,Drama")
GODFATHER_AKAS = [  # (id, region, language, title)
    ("tt0000020", "US", "\\N", "The Godfather"), ("tt0000020", "XWW", "en", "The Godfather"),
    ("tt0000020", "CSHH", "cs", "Kmotr"), ("tt0000020", "CSHH", "sk", "Krstný Otec"),
    ("tt0000020", "SK", "\\N", "Krstný otec")]


@pytest.fixture()
def client(monkeypatch):
    """TestClient with TMDB replaced by canned answers (no network)."""
    from app import main, overviews, tmdb_client
    monkeypatch.setattr(tmdb_client, "request", fake_request)
    with TestClient(main.app) as c:
        yield c
    deadline = time.time() + 10  # background overview fetches must finish while the fake TMDB is still patched in
    while overviews.PENDING and time.time() < deadline:
        time.sleep(0.01)
