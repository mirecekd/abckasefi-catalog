# abckasefi-catalog/app/config.py
"""All settings come from environment variables (see .env.example)."""
import os


def _int(name, default):
    """Integer from the environment; a malformed value falls back to the default."""
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


DATA = os.environ.get("DATA_DIR", "/data")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()  # v3 api key or v4 read token
TMDB_LANG = os.environ.get("TMDB_LANGUAGE", "cs-CZ")
API_TOKEN = os.environ.get("API_TOKEN", "").strip()  # empty = no auth (LAN only!)
MIN_VOTES = _int("MIN_VOTES", 200)  # keep titles with at least this many IMDb votes
LOCAL_MIN_VOTES = _int("LOCAL_MIN_VOTES", 5)  # ... or a CZ/SK title name and at least this many
ORIGIN_MOVIE_MIN_VOTES = _int("ORIGIN_MOVIE_MIN_VOTES", 100)  # CZ/SK films only: IMDb votes needed for origin=1
IMPORT_EVERY_HOURS = _int("IMPORT_EVERY_HOURS", 168)
