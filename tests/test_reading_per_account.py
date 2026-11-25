"""Reading data (resume point, history, notes, comic view prefs), the Quick video profile and new-chapter dismissals are kept per sign-in account."""
import json

import pytest

from services import quick_video_service


def _drama(db):
    return db.create_drama(title_en="T")


def test_each_account_has_its_own_resume_point_notes_and_view_prefs(isolated_db):
    db = isolated_db
    d = _drama(db)
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    db.save_progress(d, last_page=3, percent_complete=30.0, user_id=a)
    db.save_progress(d, last_page=7, percent_complete=70.0, user_id=b)
    db.save_progress(d, last_page=1, percent_complete=10.0)
    db.save_personal_notes(d, "a's note", user_id=a)
    db.save_view_prefs(d, json.dumps({"mode": "scroll"}), user_id=b)

    assert db.get_progress(d, user_id=a)["last_page"] == 3
    assert db.get_progress(d, user_id=b)["last_page"] == 7
    assert db.get_progress(d)["last_page"] == 1
    assert db.get_personal_notes(d, user_id=a) == "a's note"
    assert db.get_personal_notes(d, user_id=b) == ""
    assert db.get_personal_notes(d) == ""
    assert db.get_view_prefs(d, user_id=a) is None
    assert db.get_view_prefs(d, user_id=b) == '{"mode": "scroll"}'


def test_continue_shelf_and_history_are_the_accounts_own(isolated_db):
    db = isolated_db
    d = _drama(db)
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    db.save_progress(d, last_line_idx=0, percent_complete=40.0, user_id=a)

    assert [r["id"] for r in db.list_continue_reading(user_id=a)] == [d]
    assert db.list_continue_reading(user_id=b) == []
    assert db.list_continue_reading() == []
    assert len(db.list_reading_history(user_id=a)) == 1
    assert db.list_reading_history(user_id=b) == []
    assert db.reading_marks_by_drama(a)[d][0] == 40.0
    assert d not in db.reading_marks_by_drama(b)

    db.save_progress(d, last_line_idx=0, percent_complete=20.0, user_id=b)
    db.clear_reading_history(user_id=b)
    assert len(db.list_reading_history(user_id=a)) == 1
    assert db.list_reading_history(user_id=b) == []


def test_reading_for_an_account_with_no_data_makes_no_profile(isolated_db):
    db = isolated_db
    d = _drama(db)
    a = db.auth_create_user("a@example.com")
    before = len(db.list_profiles())
    assert db.get_progress(d, user_id=a) is None
    assert db.get_personal_notes(d, user_id=a) == ""
    assert db.list_continue_reading(user_id=a) == []
    assert len(db.list_profiles()) == before


def test_deleting_an_account_deletes_its_reading_data(isolated_db):
    db = isolated_db
    d = _drama(db)
    a = db.auth_create_user("a@example.com")
    db.save_progress(d, last_line_idx=0, percent_complete=40.0, user_id=a)
    db.save_personal_notes(d, "x", user_id=a)
    db.set_app_setting(f"quick_video.profile_id.{a}", 5)
    db.save_progress(d, last_line_idx=0, percent_complete=10.0)
    conn = db.get_conn()
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (a,))
    conn.commit()
    conn.close()

    assert db.auth_delete_inactive_member(a)

    conn = db.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM profiles WHERE user_id = ?", (a,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM progress").fetchone()[0] == 1   # the PC's stays
        assert conn.execute("SELECT COUNT(*) FROM personal_notes").fetchone()[0] == 0
    finally:
        conn.close()
    assert db.get_app_setting(f"quick_video.profile_id.{a}") is None


def test_upgrade_gives_each_active_admin_a_copy_of_the_shared_data(isolated_db):
    db = isolated_db
    d = _drama(db)
    admin = db.auth_create_user("admin@example.com", is_admin=True)
    member = db.auth_create_user("m@example.com")
    db.save_progress(d, last_page=4, last_line_idx=0, percent_complete=40.0)
    db.save_personal_notes(d, "shared before")
    conn = db.get_conn()
    conn.execute("DROP INDEX idx_profiles_user")
    conn.execute("ALTER TABLE profiles DROP COLUMN user_id")
    conn.commit()
    conn.close()

    db._migrate_profiles_per_account()
    db._migrate_profiles_per_account()   # a second start changes nothing

    assert db.get_progress(d, user_id=admin)["last_page"] == 4
    assert db.get_personal_notes(d, user_id=admin) == "shared before"
    assert len(db.list_reading_history(user_id=admin)) == 1
    assert db.get_progress(d, user_id=member) is None
    assert db.get_progress(d)["last_page"] == 4
    # Writes after the upgrade no longer reach the other's copy.
    db.save_progress(d, last_page=9, user_id=admin)
    assert db.get_progress(d)["last_page"] == 4


def test_quick_video_profile_is_per_account_and_falls_back_to_the_pcs(isolated_db):
    db = isolated_db
    a = db.auth_create_user("a@example.com")
    pc = quick_video_service.chosen_profile_id()
    assert pc is not None
    assert quick_video_service.chosen_profile_id(a) == pc

    other = db.save_preset("Mine", options_json='{"quick_source_language": "ja"}')
    quick_video_service.set_profile(other, a)
    assert quick_video_service.chosen_profile_id(a) == other
    assert quick_video_service.chosen_profile_id() == pc

    quick_video_service.set_profile(None, a)
    assert quick_video_service.chosen_profile_id(a) is None
    assert quick_video_service.get_settings(a)["profile_id"] is None
    assert quick_video_service.get_settings()["profile_id"] == pc


def test_new_chapter_notices_are_dismissed_per_account(isolated_db):
    from sources import store
    from sources.models import ChapterInfo
    d = _drama(isolated_db)
    store.track_series("s", "1", "Linked", drama_id=d)
    store.record_new_chapters("s", "1", [ChapterInfo("s", "1", "c1", "1"), ChapterInfo("s", "1", "c2", "2")])
    first = store.list_notifications()[-1]["id"]

    store.dismiss_notification(first, 7)

    assert len(store.list_notifications(user_id=7)) == 1
    assert len(store.list_notifications(user_id=8)) == 2
    assert len(store.list_notifications()) == 2
    assert [n["dismissed"] for n in store.list_notifications(include_dismissed=True, user_id=7)
            if n["id"] == first] == [True]
    assert store.new_chapter_counts_by_drama(7) == {d: 1}
    assert store.new_chapter_counts_by_drama(8) == {d: 2}
    assert store.new_chapter_counts_by_drama() == {d: 2}


@pytest.mark.parametrize("who", ["pc", "account"])
def test_a_user_backup_carries_only_the_owners_reading_data(isolated_db, who):
    pytest.importorskip("fastapi")
    from services import library_user_backup_service as lub
    db = isolated_db
    owner = None if who == "pc" else db.auth_create_user("o@example.com")
    other = db.auth_create_user("x@example.com")
    d = db.create_drama(title_en="Mine", owner_user_id=owner)
    db.save_progress(d, last_page=2, percent_complete=20.0, last_line_idx=0)
    db.save_progress(d, last_page=5, percent_complete=50.0, last_line_idx=0, user_id=other)
    if owner:
        db.save_progress(d, last_page=8, percent_complete=80.0, last_line_idx=0, user_id=owner)

    import shutil
    import sqlite3
    path = str(db.LIBRARY_DIR) + "/copy.db"
    shutil.copyfile(db.DB_PATH, path)
    assert lub._user_backup_filter(path, owner) == [d]
    conn = sqlite3.connect(path)
    try:
        pages = [r[0] for r in conn.execute("SELECT last_page FROM progress")]
        profile_ids = {r[0] for r in conn.execute("SELECT profile_id FROM progress")}
        default = conn.execute("SELECT MIN(id) FROM profiles").fetchone()[0]
    finally:
        conn.close()
    assert pages == [8 if owner else 2]
    assert profile_ids == {default}


def test_watch_position_is_per_account_and_puts_the_title_on_the_continue_shelf(isolated_db):
    from core import Line
    from services import library_service, reader_service
    db = isolated_db
    d = db.create_drama(title_en="Talk", media_type="audio_drama")
    db.save_lines(d, [Line(idx=0, start=0.0, end=100.0, zh="你")])
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")

    assert reader_service.get_watch_position(d, a) == {"drama_id": d, "seconds": 0.0, "updated_at": None}
    saved = reader_service.save_watch_position(d, 40.0, a)

    assert saved["seconds"] == 40.0 and saved["updated_at"]
    assert reader_service.get_watch_position(d, b)["seconds"] == 0.0
    assert reader_service.get_watch_position(d)["seconds"] == 0.0
    shelf = library_service.list_continue_reading(principal={"user_id": a, "is_admin": True})
    assert [(i["drama_id"], i["audio_position_seconds"], i["percent_complete"]) for i in shelf] == [(d, 40.0, 40.0)]
    assert library_service.list_continue_reading(principal={"user_id": b, "is_admin": True}) == []
    assert db.list_reading_history(user_id=a) == []   # a player saves often; the history stays clean


def test_restoring_a_title_brings_back_each_accounts_own_reading_data(isolated_db):
    from tests.auto_backup_helpers import _delete_drama, _restore, _snap
    db = isolated_db
    d = db.create_drama(title_en="Restored")
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    db.save_personal_notes(d, "a's note", user_id=a)
    db.save_personal_notes(d, "b's note", user_id=b)
    db.save_personal_notes(d, "the PC's note")
    _snap()
    _delete_drama(d)

    _restore(d)

    assert db.get_personal_notes(d, user_id=a) == "a's note"
    assert db.get_personal_notes(d, user_id=b) == "b's note"
    assert db.get_personal_notes(d) == "the PC's note"
