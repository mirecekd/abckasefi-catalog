# abckasefi-catalog/app/filters.py
"""Genre and origin filters of the browse routes: accepted values and the genre counts."""
from collections import Counter

from fastapi import HTTPException

from . import db

GENRES = ("Action", "Adventure", "Animation", "Biography", "Comedy", "Crime", "Documentary", "Drama", "Family",
          "Fantasy", "Film-Noir", "Game-Show", "History", "Horror", "Music", "Musical", "Mystery", "News",
          "Reality-TV", "Romance", "Sci-Fi", "Short", "Sport", "Talk-Show", "Thriller", "War", "Western")
_BY_LOWER = {g.lower(): g for g in GENRES}
ORIGINS = ("czsk",)  # Czech, Slovak and Czechoslovak series (titles.origin = 1)


def check_genre(genre):
    """Canonical IMDb genre name ('' = no filter, case-insensitive input); anything outside GENRES is HTTP 400."""
    if not genre:
        return ""
    found = _BY_LOWER.get(genre.strip().lower())
    if not found:
        raise HTTPException(400, "genre must be one of " + ", ".join(GENRES))
    return found


def check_origin(origin):
    """True for origin=czsk, False for none; any other value is HTTP 400."""
    if origin and origin not in ORIGINS:
        raise HTTPException(400, "origin must be " + ORIGINS[0])
    return bool(origin)


def genre_counts(kind, origin):
    """[{'genre', 'n'}] for the genres present in one type (optionally only the CZ/SK origin), biggest first."""
    with db.catalog() as conn:
        if conn is None:
            return None
        counts = Counter()
        for row in conn.execute("SELECT genres FROM titles WHERE type=? AND (?=0 OR origin=1) AND genres!=''",
                                (kind, int(origin))):
            counts.update(row[0].split(","))
    return [{"genre": g, "n": n} for g, n in counts.most_common() if g in _BY_LOWER.values()]
