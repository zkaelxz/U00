"""Vocab lookups and wiki entries find their first line by its id, so a split, merge or delete
earlier in the title doesn't point them at a different line; and library.db's compaction."""
import dataclasses
import sqlite3

from core import Line, lines_from_rows
import db
from db import compact


def _drama_with_lines(n=5):
    did = db.create_drama(title_en="T", media_type="novel", source_language="zh")
    db.save_lines(did, [Line(idx=i, start=0, end=0, zh=f"行{i}", en=f"line {i}") for i in range(n)])
    return did, lines_from_rows(db.load_lines(did))


def _drop_first_line(did, lines):
    db.save_lines(did, [dataclasses.replace(ln, idx=i) for i, ln in enumerate(lines[1:])])


def test_vocab_position_follows_the_line_when_earlier_lines_are_deleted(isolated_db):
    did, lines = _drama_with_lines()
    target = lines[3]
    db.save_vocab_lookup(did, "词", "ci", ["word"], "zh", target.idx, target.id)

    _drop_first_line(did, lines)

    row = db.list_vocab_lookups(did)[0]
    assert db.load_lines(did)[row["first_seen_line_idx"]]["id"] == target.id


def test_vocab_whose_line_was_deleted_has_no_position(isolated_db):
    did, lines = _drama_with_lines()
    db.save_vocab_lookup(did, "词", "ci", ["word"], "zh", lines[0].idx, lines[0].id)

    _drop_first_line(did, lines)

    assert db.list_vocab_lookups(did)[0]["first_seen_line_idx"] is None


def test_vocab_saved_without_an_id_keeps_its_stored_position(isolated_db):
    did, _lines = _drama_with_lines()
    db.save_vocab_lookup(did, "词", "ci", ["word"], "zh", 2)
    assert db.list_vocab_lookups(did)[0]["first_seen_line_idx"] == 2


def test_wiki_spoiler_limit_follows_the_line_not_its_old_position(isolated_db):
    did, lines = _drama_with_lines()
    late = lines[4]
    db.upsert_wiki_entry(did, "character", "Late", first_seen_line_idx=late.idx,
                         first_seen_line_id=late.id)
    # Two lines go before it: it now sits at position 2, still after position 1.
    db.save_lines(did, [dataclasses.replace(ln, idx=i) for i, ln in enumerate(lines[2:])])

    assert [e["name"] for e in db.list_wiki_entries(did, spoiler_limit_line_idx=1)] == []
    shown = db.list_wiki_entries(did, spoiler_limit_line_idx=2)
    assert [(e["name"], e["first_seen_line_idx"]) for e in shown] == [("Late", 2)]


def _bloat(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS junk (x BLOB)")
    conn.executemany("INSERT INTO junk VALUES (?)", [(b"x" * 4000,)] * 400)
    conn.execute("DELETE FROM junk")
    conn.close()


def test_compaction_shrinks_a_mostly_free_library(isolated_db, monkeypatch):
    import os
    monkeypatch.setattr(compact, "MIN_RECLAIM_BYTES", 1024)
    _bloat(db.DB_PATH)
    before = os.path.getsize(db.DB_PATH)

    assert compact.compact_if_worthwhile() is True
    assert os.path.getsize(db.DB_PATH) < before / 2


def test_compaction_leaves_a_small_free_area_alone(isolated_db):
    _bloat(db.DB_PATH)
    assert compact.compact_if_worthwhile() is False


def test_wiki_entry_stays_hidden_when_its_line_is_deleted(isolated_db):
    did, lines = _drama_with_lines()
    db.upsert_wiki_entry(did, "character", "Late", first_seen_line_idx=4, first_seen_line_id=lines[4].id)
    db.save_lines(did, lines[:4])    # the anchored last line is gone

    assert db.list_wiki_entries(did, spoiler_limit_line_idx=1) == []


def test_wiki_entry_saved_before_ids_keeps_its_position_when_upserted_again(isolated_db):
    did, lines = _drama_with_lines()
    db.upsert_wiki_entry(did, "character", "Old", first_seen_line_idx=1)

    db.upsert_wiki_entry(did, "character", "Old", first_seen_line_idx=4, first_seen_line_id=lines[4].id)

    row = db.list_wiki_entries(did)[0]
    assert row["first_seen_line_idx"] == 1 and row["first_seen_line_id"] is None
    assert [e["name"] for e in db.list_wiki_entries(did, spoiler_limit_line_idx=1)] == ["Old"]


def test_upgrade_gives_position_only_rows_their_line_id(isolated_db):
    did, lines = _drama_with_lines()
    db.upsert_wiki_entry(did, "character", "Old", first_seen_line_idx=2)
    db.save_vocab_lookup(did, "词", "ci", ["w"], "zh", 3)
    from db import migrations
    import contextlib
    with contextlib.closing(db.get_conn()) as conn:
        for table in ("wiki_entries", "vocab_lookups"):
            migrations._backfill_first_seen_ids(conn, table)
        conn.commit()
    assert db.list_wiki_entries(did)[0]["first_seen_line_id"] == lines[2].id
    assert db.list_vocab_lookups(did)[0]["first_seen_line_id"] == lines[3].id


def test_restore_remaps_first_seen_line_ids():
    from services import drama_restore_service as drs
    row = {"first_seen_line_id": 7}
    drs._remap_first_seen("wiki_entries", row, {7: 70})
    assert row["first_seen_line_id"] == 70
    other = {"first_seen_line_id": 7}
    drs._remap_first_seen("lines", other, {7: 70})
    assert other["first_seen_line_id"] == 7


def test_compaction_skips_a_library_another_process_has_open(isolated_db, monkeypatch):
    monkeypatch.setattr(compact, "MIN_RECLAIM_BYTES", 1024)
    _bloat(db.DB_PATH)
    other = sqlite3.connect(db.DB_PATH)
    try:
        other.execute("SELECT count(*) FROM sqlite_master").fetchone()
        other.execute("BEGIN")
        other.execute("SELECT count(*) FROM junk").fetchone()
        assert compact.compact_if_worthwhile() is False
    finally:
        other.close()


def test_compaction_announces_itself_and_needs_room_for_two_copies(isolated_db, monkeypatch):
    import shutil
    monkeypatch.setattr(compact, "MIN_RECLAIM_BYTES", 1024)
    _bloat(db.DB_PATH)
    told = []
    assert compact.compact_if_worthwhile(notify=told.append) is True
    assert told == [compact.NOTICE]

    _bloat(db.DB_PATH)
    conn = sqlite3.connect(db.DB_PATH)
    free, total = compact.reclaimable_bytes(conn)
    conn.close()
    room = int(1.5 * (total - free)) + 1024     # enough for one copy, not two
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda p: shutil._ntuple_diskusage(10**9, 10**9, room))
    told.clear()
    assert compact.compact_if_worthwhile(notify=told.append) is False and told == []
