# abckasefi-catalog/tests/test_db_import.py
"""Importer output and catalog rows (no HTTP)."""
from app import common, db, importer


def _row(title_id):
    row = db.get(title_id)
    assert row is not None, title_id
    return row


def test_filter_keeps_popular_and_czech_named(built):
    # kept: tt1, tt5 (300 votes), tt2 (7 votes but a CZ name), tt3, tt7; dropped: tt4 adult, tt6 (50 votes, no CZ name),
    # tt42 (series, 30 votes, no CZ/SK origin). Kept only for their CZ/SK origin: tt40, tt41 (30 votes), tt43 (no rating)
    assert db.stats()["counts"] == {"movie": 11, "series": 5}
    assert db.get("tt0000004") is None
    assert db.get("tt0000006") is None


def test_working_title_never_beats_display_title(built):
    # tt1 has a later CZ row typed "working": the real IMDb display title must win (regression: Pelisky was "Hovno hori")
    assert _row("tt0000001")["name"] == "Pelíšky"
    assert db.search('"pracovni"*', "", "popular", 5) == []


def test_czech_name_original_and_letter_without_diacritics(built):
    row = _row("tt0000001")
    assert (row["name"], row["orig"], row["letter"], row["local"]) == ("Pelíšky", "Pelisky", "P", 1)
    assert _row("tt0000005")["letter"] == "0-9"  # "7 dní" -> digits bucket
    assert _row("tt0000002")["letter"] == "C"  # "Český film" sorts under C, not after Z


def test_importer_helpers():
    assert importer.letter_of(importer.sortkey("Čelisti")) == "C"
    assert importer.letter_of(importer.sortkey("¿Qué?")) == "Q"
    assert importer.letter_of(importer.sortkey("1917")) == "0-9"
    assert importer.letter_of(importer.sortkey("Ørnen")) in ("0-9", "O")


def test_fts_query_quoting():
    assert common.fts_query("Šílené  příběhy") == '"silene" "pribehy"*'
    assert common.fts_query("!!!") == ""


def test_czech_title_found_via_language_cs_when_region_is_old_czechoslovakia(built):
    # regression: The Godfather has Czech "Kmotr" only as language=cs/region=CSHH; the importer looked at region CZ
    # only and showed the Slovak "Krstny otec"
    row = _row("tt0000020")
    assert (row["name"], row["name_cs"], row["name_sk"], row["name_en"]) == ("Kmotr", "Kmotr", "Krstný Otec", "The Godfather")
    assert row["letter"] == "K"
