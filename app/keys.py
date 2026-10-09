# abckasefi-catalog/app/keys.py
"""Text keys derived from a title: `sortkey` (ordering, A-Z letter) and `skey` (the A-Z drill-down prefix key)."""
import re
import unicodedata

NOT_ALNUM = re.compile(r"[^a-z0-9]")
PREFIX_MAX = 10


def sortkey(name):
    """Lower-case text without diacritics, used for ordering and for the A-Z letter."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return plain.casefold().strip()


def skey(name):
    """sortkey with everything but [a-z0-9] removed ("A Minute" -> "aminute"); prefixes are counted over it.

    Idempotent, so it can be applied to an already stored sortkey as well as to a raw name."""
    return NOT_ALNUM.sub("", sortkey(name))
