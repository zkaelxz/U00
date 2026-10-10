"""Tests for services/drama_service.py (Migration Slice 35)."""
import pytest

import db
from services import drama_service as ds
from services.service_errors import InvalidInputError, NotFoundError


def _count():
    return len(db.list_dramas())


def test_create_basic(isolated_db):
    d = ds.create_drama(source_language="ja", title_en="T", media_type="anime")
    assert d["title_en"] == "T" and d["source_language"] == "ja"
    assert d["media_type"] == "anime" and d["preset_defaults"] is None


@pytest.mark.parametrize("lang", [None, "", "en", "ZH"])
def test_create_bad_language(isolated_db, lang):
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language=lang)
    assert _count() == 0


def test_create_bad_media_type(isolated_db):
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language="zh", media_type="podcast")


def test_create_unknown_series_and_preset(isolated_db):
    with pytest.raises(NotFoundError):
        ds.create_drama(source_language="zh", series_id=99)
    with pytest.raises(NotFoundError):
        ds.create_drama(source_language="zh", preset_id=99)
    assert _count() == 0


def test_series_id_and_new_name_rejected(isolated_db):
    sid = db.get_or_create_series("S")
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language="zh", series_id=sid, new_series_name="X")
    assert _count() == 0


def test_blank_new_series_name_rejected(isolated_db):
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language="zh", new_series_name="   ")
    assert _count() == 0 and db.list_series() == []


def test_new_series_created_and_assigned(isolated_db):
    d = ds.create_drama(source_language="zh", new_series_name=" Saga ")
    series = db.list_series()
    assert [s["name"] for s in series] == ["Saga"]
    assert d["series_id"] == series[0]["id"]


def test_existing_series_assigned(isolated_db):
    sid = db.get_or_create_series("S")
    assert ds.create_drama(source_language="zh", series_id=sid)["series_id"] == sid


def test_preset_applied(isolated_db):
    pid = db.save_preset("P", translation_engine="gemini", engine_model="m",
                         style_preset="casual", locale="en-GB",
                         default_female_pronouns=True, include_genre_notes=False)
    d = ds.create_drama(source_language="ko", preset_id=pid)
    assert d["translation_engine"] == "gemini"
    assert d["preset_defaults"] == {
        "style_preset": "casual", "locale": "en-GB",
        "default_female_pronouns": True, "include_genre_notes": False,
        "engine_model": "m"}


def test_preset_without_engine_leaves_default(isolated_db):
    pid = db.save_preset("P", style_preset="casual", engine_model="m")
    d = ds.create_drama(source_language="zh", preset_id=pid)
    assert d["translation_engine"] == db.get_drama(d["id"])["translation_engine"]
    assert d["preset_defaults"]["style_preset"] == "casual"
    # The tab applies a preset's model only to the preset's own engine.
    assert d["preset_defaults"]["engine_model"] is None


@pytest.mark.parametrize("key", ["status", "content_mode", "source_language",
                                 "audio_filename", "translation_engine",
                                 "personal_notes", "id; DROP TABLE dramas"])
def test_non_whitelisted_key_rejected(isolated_db, key):
    did = db.create_drama(title_en="A", source_language="zh")
    before = db.get_drama(did)
    with pytest.raises(InvalidInputError) as e:
        ds.update_drama_metadata(did, **{key: "evil-value"})
    assert "evil-value" not in str(e.value)
    assert db.get_drama(did) == before
    assert _count() == 1


def test_partial_update_touches_only_passed(isolated_db):
    did = db.create_drama(title_en="A", author="Au", source_language="zh")
    db.update_drama(did, updated_at="2000-01-01T00:00:00")
    before = db.get_drama(did)
    d = ds.update_drama_metadata(did, studio="Studio", publication_status="ongoing",
                                 chapter_count=5, episode_number=2, media_type="novel")
    assert d["studio"] == "Studio" and d["publication_status"] == "ongoing"
    assert d["chapter_count"] == 5 and d["episode_number"] == 2
    assert d["title_en"] == "A" and d["author"] == "Au"
    assert d["updated_at"] != before["updated_at"]


def test_none_means_not_passed_and_empty_call_is_noop(isolated_db):
    did = db.create_drama(title_en="A", source_language="zh")
    before = db.get_drama(did)
    assert ds.update_drama_metadata(did, title_en=None) == before
    assert ds.update_drama_metadata(did) == before


def test_clear_text_field_with_empty_string(isolated_db):
    did = db.create_drama(title_en="A", author="Au", source_language="zh")
    d = ds.update_drama_metadata(did, author="")
    assert d["author"] == "" and d["title_en"] == "A"


def test_zero_clears_int(isolated_db):
    did = db.create_drama(source_language="zh", chapter_count=4)
    assert ds.update_drama_metadata(did, chapter_count=0)["chapter_count"] is None


def test_update_unknown_drama(isolated_db):
    with pytest.raises(NotFoundError):
        ds.update_drama_metadata(42, title_en="x")
    assert _count() == 0


@pytest.mark.parametrize("field", ["chapter_count", "episode_number"])
@pytest.mark.parametrize("bad", ["3", 1.5, -1, True])
def test_int_type_errors(isolated_db, field, bad):
    did = db.create_drama(source_language="zh")
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, **{field: bad})


def test_text_type_error_and_bad_enums(isolated_db):
    did = db.create_drama(source_language="zh")
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, title_en=5)
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, publication_status="dead")
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, media_type="nope")


def test_update_series_id(isolated_db):
    did = db.create_drama(source_language="zh")
    with pytest.raises(NotFoundError):
        ds.update_drama_metadata(did, series_id=77)
    sid = db.get_or_create_series("S")
    assert ds.update_drama_metadata(did, series_id=sid)["series_id"] == sid


def test_detail_has_no_filesystem_path(isolated_db):
    d = ds.create_drama(source_language="zh", title_en="A")
    d = ds.update_drama_metadata(d["id"], summary="s")
    for v in d.values():
        if isinstance(v, str):
            assert db.LIBRARY_DIR not in v


# --- Hardening H1 -----------------------------------------------------------

BIG = 10**30
HOSTILE = "evil\n" + "x" * 5000 + "\u202e\u2603"


@pytest.mark.parametrize("kw", [{"series_id": BIG}, {"preset_id": BIG}])
def test_h1_create_oversized_ids(isolated_db, kw):
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language="zh", **kw)
    assert _count() == 0


@pytest.mark.parametrize("field", ["chapter_count", "episode_number", "series_id"])
def test_h1_update_oversized_ints(isolated_db, field):
    did = ds.create_drama(source_language="zh")["id"]
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, **{field: BIG})


def test_h1_update_oversized_drama_id(isolated_db):
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(BIG, title_en="x")


def test_h1_text_caps_and_no_echo(isolated_db):
    with pytest.raises(InvalidInputError) as e:
        ds.create_drama(source_language="zh", title_en=HOSTILE)
    assert "evil" not in str(e.value)
    did = ds.create_drama(source_language="zh")["id"]
    for field, value in (("summary", "a" * 5001), ("project_instructions", "a" * 5001),
                         ("author", "a" * 301), ("source_url", "http://" + "a" * 2001),
                         ("custom_tags", "a" * 2001)):
        with pytest.raises(InvalidInputError):
            ds.update_drama_metadata(did, **{field: value})
    ds.update_drama_metadata(did, summary="a" * 5000, author="a" * 300)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x"])
def test_h1_source_url_scheme(isolated_db, url):
    did = ds.create_drama(source_language="zh")["id"]
    with pytest.raises(InvalidInputError) as e:
        ds.update_drama_metadata(did, source_url=url)
    assert url not in str(e.value)
    assert ds.update_drama_metadata(did, source_url="https://ok.example/a")["source_url"]
    assert ds.update_drama_metadata(did, source_url="")["source_url"] in ("", None)


def test_h1_titles_stripped_and_blank_titles_allowed(isolated_db):
    d = ds.create_drama(source_language="zh", title_en="  Hi  ", title_zh="\u4f60 ")
    assert d["title_en"] == "Hi" and d["title_zh"] == "\u4f60"
    assert ds.create_drama(source_language="zh")["id"]  # the tab allows both blank
    assert ds.update_drama_metadata(d["id"], title_en=" B ")["title_en"] == "B"


def test_h1_unknown_field_message_never_echoes_key(isolated_db):
    did = ds.create_drama(source_language="zh")["id"]
    with pytest.raises(InvalidInputError) as e:
        ds.update_drama_metadata(did, secret_key_name="x")
    assert "secret_key_name" not in str(e.value)


def test_h1_failed_create_leaves_no_stray_series(isolated_db):
    with pytest.raises(InvalidInputError):
        ds.create_drama(source_language="zh", new_series_name="S", title_en="a" * 301)
    with pytest.raises(NotFoundError):
        ds.create_drama(source_language="zh", new_series_name="S", preset_id=999)
    assert db.list_series() == []
    d = ds.create_drama(source_language="zh", new_series_name="S")
    assert d["series_id"] == db.list_series()[0]["id"]

# ---- Slice 36: delete ----------------------------------------------------
import os
import time

import background_jobs
from services.service_errors import ConflictError


def _drama_with_files():
    did = db.create_drama(title_en="Doomed", source_language="zh")
    refs = os.path.join(db.drama_dir(did), "voice_refs")
    os.makedirs(refs)
    with open(os.path.join(refs, "x"), "wb") as f:
        f.write(b"clip")
    return did, db.drama_dir(did)


def _intact(did, folder):
    return db.get_drama(did) is not None and os.path.isfile(os.path.join(folder, "voice_refs", "x"))


def test_delete_unknown_is_not_found_even_without_confirm(isolated_db):
    with pytest.raises(NotFoundError):
        ds.delete_drama(999)


@pytest.mark.parametrize("confirm,text", [(False, "DELETE"), (True, ""), (True, "delete"),
                                          (True, "DELETE "), (1, "DELETE"), (None, "DELETE")])
def test_delete_bad_confirmation(isolated_db, confirm, text):
    did, folder = _drama_with_files()
    with pytest.raises(InvalidInputError):
        ds.delete_drama(did, confirm=confirm, confirm_text=text)
    assert _intact(did, folder)


def test_delete_ok(isolated_db):
    did, folder = _drama_with_files()
    assert ds.delete_drama(did, confirm=True, confirm_text="DELETE") == {
        "deleted": True, "drama_id": did}
    assert db.get_drama(did) is None and not os.path.exists(folder)


def test_delete_blocked_by_in_process_job(isolated_db, monkeypatch):
    did, folder = _drama_with_files()
    monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda _id: True)
    with pytest.raises(ConflictError):
        ds.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert _intact(did, folder)


def test_delete_blocked_by_fresh_job_record(isolated_db):
    did, folder = _drama_with_files()
    db.save_job_record(f"transcribe_{did}", "running")
    with pytest.raises(ConflictError):
        ds.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert _intact(did, folder)


def test_delete_stale_job_record_does_not_block(isolated_db, monkeypatch):
    did, folder = _drama_with_files()
    db.save_job_record(f"transcribe_{did}", "running")
    real = time.time()
    monkeypatch.setattr(ds.time, "time", lambda: real + ds.STALE_JOB_RECORD_SECONDS + 60)
    ds.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert db.get_drama(did) is None


def test_delete_other_dramas_job_record_does_not_block(isolated_db):
    did, _ = _drama_with_files()
    other = db.create_drama(title_en="Other", source_language="zh")
    db.save_job_record(f"transcribe_{other}", "running")
    db.save_job_record(f"transcribe_{did}", "done")
    ds.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert db.get_drama(did) is None and db.get_drama(other) is not None


def test_delete_folder_removal_fails_still_succeeds_with_warning(isolated_db, monkeypatch):
    did, folder = _drama_with_files()

    def boom(path, *a, **k):
        raise OSError(f"in use: {path}")
    monkeypatch.setattr(ds.shutil, "rmtree", boom)
    res = ds.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert res["deleted"] is True and res["drama_id"] == did
    assert res["warning"] and folder not in res["warning"]
    assert db.get_drama(did) is None


def test_delete_db_failure_restores_folder(isolated_db, monkeypatch):
    did, folder = _drama_with_files()

    def boom(_id):
        raise RuntimeError("db down")
    monkeypatch.setattr(ds.db, "delete_drama", boom)
    from services.service_errors import ServiceError
    with pytest.raises(ServiceError) as ei:
        ds.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert folder not in str(ei.value)
    assert _intact(did, folder)


def test_delete_rename_failure_leaves_everything(isolated_db, monkeypatch):
    did, folder = _drama_with_files()

    def boom(*a, **k):
        raise OSError("locked")
    monkeypatch.setattr(ds.os, "rename", boom)
    from services.service_errors import ServiceError
    with pytest.raises(ServiceError):
        ds.delete_drama(did, confirm=True, confirm_text="DELETE")
    assert _intact(did, folder)


# --- Auth slice B1: series assignment goes through ownership_service ---

def _user(email):
    uid = db.auth_create_user(email)
    return uid, {"user_id": uid, "is_admin": False, "is_local_owner": False}


def test_create_and_move_respect_series_visibility(isolated_db):
    from services.service_errors import ConflictError
    a_id, a = _user("a@example.com")
    b_id, b = _user("b@example.com")
    secret = db.get_or_create_series("Secret", owner_user_id=a_id, is_private=True)
    with pytest.raises(NotFoundError):
        ds.create_drama(source_language="zh", title_zh="x", series_id=secret, principal=b)
    with pytest.raises(ConflictError, match="That series name is taken"):
        ds.create_drama(source_language="zh", title_zh="x", new_series_name="Secret",
                        principal=b)
    assert len(db.list_dramas()) == 0                       # refused before any write
    did = ds.create_drama(source_language="zh", title_zh="x", series_id=secret,
                          principal=a)["id"]
    assert db.get_drama(did)["series_id"] == secret
    # A tries to hide B's shared drama in A's private series: refused, untouched.
    b_drama = db.create_drama(title_zh="b", owner_user_id=b_id)
    with pytest.raises(ConflictError):
        ds.update_drama_metadata(b_drama, principal=a, series_id=secret, title_en="t")
    assert db.get_drama(b_drama)["series_id"] is None
    assert db.get_drama(b_drama)["title_en"] in (None, "")
    with pytest.raises(NotFoundError):
        ds.update_drama_metadata(b_drama, principal=b, series_id=secret)
    # Auth off (principal None) still sees everything, but the owner rule holds.
    with pytest.raises(ConflictError):
        ds.update_drama_metadata(b_drama, series_id=secret)


def test_move_clears_the_dramas_own_private_flag(isolated_db):
    b_id, b = _user("b@example.com")
    sid = db.get_or_create_series("Open", owner_user_id=b_id)
    did = db.create_drama(title_zh="x", owner_user_id=b_id, is_private=1)
    ds.update_drama_metadata(did, principal=b, series_id=sid)
    assert db.get_item_ownership("drama", did)["is_private"] == 0


def test_new_series_refused_after_precheck_creates_nothing(isolated_db, monkeypatch):
    # Security review LOW-A: the name can be taken (or made private) between
    # the pre-check and the series step; the new drama must be undone.
    from services import ownership_service
    from services.service_errors import ConflictError

    def refuse(principal, name):
        raise ConflictError("That series name is taken")

    monkeypatch.setattr(ownership_service, "get_or_create_series_for", refuse)
    with pytest.raises(ConflictError):
        ds.create_drama(source_language="zh", title_zh="x", new_series_name="Race")
    assert db.list_dramas() == []


# --- Parity P11/X09: "+ New series" on update, and taking a drama out of its series ---

def test_update_new_series_name_creates_and_assigns(isolated_db):
    did = ds.create_drama(source_language="zh")["id"]
    d = ds.update_drama_metadata(did, new_series_name="  Saga ", title_en="T")
    assert [s["name"] for s in db.list_series()] == ["Saga"]
    assert d["series_id"] == db.list_series()[0]["id"] and d["title_en"] == "T"
    # an existing name is reused, not duplicated
    other = ds.create_drama(source_language="zh")["id"]
    assert ds.update_drama_metadata(other, new_series_name="Saga")["series_id"] == d["series_id"]
    assert len(db.list_series()) == 1


@pytest.mark.parametrize("kw", [{"new_series_name": "   "},
                                {"new_series_name": "x" * 301},
                                {"new_series_name": "X", "series_id": 1}])
def test_update_new_series_name_rejected(isolated_db, kw):
    db.get_or_create_series("S")
    did = ds.create_drama(source_language="zh")["id"]
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, title_en="changed", **kw)
    assert db.list_series()[0]["name"] == "S" and len(db.list_series()) == 1
    assert db.get_drama(did)["title_en"] in (None, "")


def test_update_new_series_refused_leaves_no_stray_series(isolated_db):
    from services import ownership_service
    from services.service_errors import ConflictError
    a_id, a = _user("a@example.com")
    b_id, _b = _user("b@example.com")
    b_drama = db.create_drama(title_zh="b", owner_user_id=b_id)
    # A doesn't share by default, so A's new series would be private and
    # can't take B's drama: refused before the series is created.
    ownership_service.set_share_by_default(a, False)
    assert ownership_service.new_item_defaults(a)["is_private"] == 1
    with pytest.raises(ConflictError):
        ds.update_drama_metadata(b_drama, principal=a, new_series_name="Mine")
    assert db.list_series() == [] and db.get_drama(b_drama)["series_id"] is None
    # A name taken by a series A can't see is refused, not joined.
    db.get_or_create_series("Hidden", owner_user_id=b_id, is_private=True)
    own = db.create_drama(title_zh="a", owner_user_id=a_id)
    with pytest.raises(ConflictError, match="taken"):
        ds.update_drama_metadata(own, principal=a, new_series_name="Hidden")
    assert db.get_drama(own)["series_id"] is None


def test_update_series_id_zero_unassigns(isolated_db):
    sid = db.get_or_create_series("S")
    did = ds.create_drama(source_language="zh", series_id=sid)["id"]
    assert ds.update_drama_metadata(did, series_id=0)["series_id"] is None
    assert db.get_drama(did)["series_id"] is None
    assert ds.update_drama_metadata(did, series_id=0)["series_id"] is None   # no-op
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, series_id=-1)
    with pytest.raises(InvalidInputError):
        ds.update_drama_metadata(did, series_id=False)


def test_leaving_a_private_series_keeps_the_drama_private(isolated_db):
    a_id, a = _user("a@example.com")
    b_id, b = _user("b@example.com")
    secret = db.get_or_create_series("Secret", owner_user_id=a_id, is_private=True)
    open_ = db.get_or_create_series("Open", owner_user_id=a_id)
    hidden = db.create_drama(title_zh="h", owner_user_id=a_id, series_id=secret)
    shown = db.create_drama(title_zh="s", owner_user_id=a_id, series_id=open_)
    ds.update_drama_metadata(hidden, principal=a, series_id=0)
    ds.update_drama_metadata(shown, principal=a, series_id=0)
    assert db.get_item_ownership("drama", hidden)["is_private"] == 1
    assert db.get_item_ownership("drama", shown)["is_private"] == 0
    visible_to_b = {d["id"] for d in db.list_dramas(visible_to=b_id)}
    assert hidden not in visible_to_b and shown in visible_to_b
    # B can't take A's hidden drama out of anything.
    with pytest.raises(NotFoundError):
        ds.update_drama_metadata(hidden, principal=b, series_id=0)


@pytest.mark.parametrize("lang,expected", [("zh", True), ("ja", True), ("ko", False)])
def test_split_by_sentences_defaults_on_for_chinese_and_japanese(isolated_db, lang, expected):
    d = ds.create_drama(source_language=lang)
    assert bool(db.get_drama(d["id"])["split_by_sentences"]) is expected
