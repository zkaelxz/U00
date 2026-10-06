"""Import chosen dramas from a backup file (services/backup_import_service.py,
POST /api/backups/import/list and /import). No network, no models."""

import contextlib
import io
import json
import logging
import os
import shutil
import sqlite3
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import auto_backup_service as abs_
from services import backup_import_service as bis
from services import library_admin_service as las

OK = {"confirm": "true", "confirm_text": "RESTORE"}
REMOTE = "https://baihe.example.com"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})   # required on multipart local_only POSTs


def _ins(c, table, **row):
    cols = ", ".join(f'"{k}"' for k in row)
    marks = ", ".join("?" for _ in row)
    return c.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})', list(row.values())).lastrowid


def _conn():
    return contextlib.closing(sqlite3.connect(db.DB_PATH))


def _world(owner=None):
    """Two dramas in series "Saga" (glossary, a series character) and one
    without a series, with lines, characters, a note and profile progress."""
    with _conn() as c:
        sid = _ins(c, "series", name="Saga", created_at="x", owner_user_id=owner, is_private=0)
        sc = _ins(c, "series_characters", series_id=sid, character_name="Lin", created_at="x")
        _ins(c, "glossary_terms", series_id=sid, term_original="剑", term_translation="sword")
        pid = _ins(c, "profiles", name="reader")
        ids = []
        for title, series in (("Alpha", sid), ("Beta", sid), ("Gamma", None)):
            did = _ins(c, "dramas", title_en=title, title_zh=title + "中", media_type="audio",
                       series_id=series, owner_user_id=owner, is_private=0, created_at="x",
                       updated_at="x", notion_page_id="page-" + title)
            for i in range(3):
                _ins(c, "lines", drama_id=did, idx=i, start=float(i), end=i + .5,
                     zh=f"{title}-zh-{i}", en=f"{title}-en-{i}")
            _ins(c, "characters", drama_id=did, speaker_label="S0", character_name="Lin",
                 series_character_id=sc if series else None)
            _ins(c, "translation_notes", drama_id=did, line_id=None, line_idx=1, term="t",
                 note="n", created_at="x")
            _ins(c, "progress", drama_id=did, profile_id=pid, last_line_idx=1,
                 percent_complete=10.0, last_accessed_at="x")
            ids.append(did)
        c.commit()
    return ids


def _manual_zip(media=None) -> bytes:
    """A manual 'Back up library' zip: library.db plus dramas/<id>/ files, no manifest."""
    for did, name in (media or {}).items():
        d = os.path.join(db.DRAMAS_DIR, str(did))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(b"media-" + name.encode())
    dest = os.path.join(db.LIBRARY_DIR, "manual.zip")
    las.write_backup_zip(dest, include_media=True)
    with open(dest, "rb") as fh:
        data = fh.read()
    os.remove(dest)
    return data


def _fixture_zip(build) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        build(zf)
    return buf.getvalue()


def _post_list(client, data, name="b.zip"):
    return client.post("/api/backups/import/list", files={"file": (name, data)})


def _post_import(client, data, ids, **form):
    form = {**OK, **form}
    return client.post("/api/backups/import", files={"file": ("b.zip", data)},
                       data={"drama_ids": [str(i) for i in ids], **form})


def _dramas():
    with _conn() as c:
        return c.execute("SELECT id, title_en, owner_user_id, is_private, series_id, notion_page_id "
                         "FROM dramas ORDER BY id").fetchall()


def _no_leak(resp):
    assert db.LIBRARY_DIR not in resp.text
    assert "library.db" not in resp.text
    for v in resp.headers.values():
        assert db.LIBRARY_DIR not in v


# ---- listing ---------------------------------------------------------------

def test_list_manual_zip_without_manifest(client):
    a, b, g = _world()
    data = _manual_zip(media={a: "audio.mp3"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "manifest.json" not in zf.namelist()
    r = _post_list(client, data)
    assert r.status_code == 200, r.text
    _no_leak(r)
    body = r.json()
    assert body["kind"] == "zip" and body["media_available"] and not body["schema_differs"]
    assert body["dramas"] == [
        {"id": a, "title": "Alpha", "media_type": "audio", "line_count": 3, "has_media": True},
        {"id": b, "title": "Beta", "media_type": "audio", "line_count": 3, "has_media": False},
        {"id": g, "title": "Gamma", "media_type": "audio", "line_count": 3, "has_media": False}]


def test_list_automatic_snapshot_copy(client):
    _world()
    abs_._backup_job(abs_.JOB_ID, False)
    path = abs_._list_copies(abs_._target_dir(create=False))[0]["path"]
    with open(path, "rb") as fh:
        r = _post_list(client, fh.read())
    assert r.status_code == 200 and len(r.json()["dramas"]) == 3
    assert not r.json()["media_available"]


def test_list_bare_database_file(client):
    _world()
    dest = os.path.join(db.LIBRARY_DIR, "x.db")
    las._sanitized_snapshot(dest)
    with open(dest, "rb") as fh:
        r = _post_list(client, fh.read(), "library.db")
    assert r.status_code == 200 and r.json()["kind"] == "database"
    assert len(r.json()["dramas"]) == 3


# ---- import ----------------------------------------------------------------

def test_import_selected_as_new_dramas_series_and_children(client):
    a, b, g = _world()
    data = _manual_zip(media={a: "audio.mp3"})
    before = _dramas()
    r = _post_import(client, data, [a, b])
    assert r.status_code == 200, r.text
    _no_leak(r)
    body = r.json()
    assert [i["source_id"] for i in body["imported"]] == [a, b]
    assert body["series_created"] == 1 and body["media_imported"] == 1
    new_a, new_b = (i["drama_id"] for i in body["imported"])
    assert len({a, b, g, new_a, new_b}) == 5
    assert _dramas()[:3] == before   # the originals are untouched
    with _conn() as c:
        sids = {r[0] for r in c.execute("SELECT series_id FROM dramas WHERE id IN (?, ?)",
                                        (new_a, new_b))}
        assert len(sids) == 1 and sids != {None}
        sid = sids.pop()
        name = c.execute("SELECT name FROM series WHERE id = ?", (sid,)).fetchone()[0]
        assert name.startswith("Saga (imported ")
        assert c.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM glossary_terms WHERE series_id = ?", (sid,)
                         ).fetchone()[0] == 1
        [(cid,)] = c.execute("SELECT id FROM series_characters WHERE series_id = ?", (sid,))
        assert c.execute("SELECT series_character_id FROM characters WHERE drama_id = ?",
                         (new_a,)).fetchone()[0] == cid
        assert c.execute("SELECT COUNT(*) FROM lines WHERE drama_id = ?", (new_a,)
                         ).fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM translation_notes WHERE drama_id = ?", (new_b,)
                         ).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM progress WHERE drama_id IN (?, ?)",
                         (new_a, new_b)).fetchone()[0] == 0   # profile progress is not copied
    assert os.path.isfile(os.path.join(db.DRAMAS_DIR, str(new_a), "audio.mp3"))
    assert not [n for n in os.listdir(db.DRAMAS_DIR) if n.startswith(".restoring")]


def test_duplicate_title_gets_restored_suffix_and_never_overwrites(client):
    a, b, g = _world()
    data = _manual_zip()
    for _ in range(2):
        r = _post_import(client, data, [g])
        assert r.json()["imported"][0]["title"].startswith("Gamma (restored 20")
    assert len(_dramas()) == 5
    assert db.get_drama(g)["title_en"] == "Gamma"


def test_no_suffix_when_title_is_free(client):
    a, b, g = _world()
    data = _manual_zip()
    db.delete_drama(g)
    r = _post_import(client, data, [g])
    assert r.json()["imported"][0]["title"] == "Gamma"


def test_owner_and_privacy_forced_and_series_never_merged(isolated_db):
    other = db.auth_create_user("other@example.com")
    me = db.auth_create_user("me@example.com")
    a, b, g = _world(owner=other)
    data = _manual_zip()
    with _conn() as c:
        live_series = {r[0] for r in c.execute("SELECT id FROM series")}
    res = bis.import_dramas(io.BytesIO(data), [a, g], confirm=True, confirm_text="RESTORE",
                            principal={"user_id": me, "is_local_owner": False})
    new = {i["source_id"]: i["drama_id"] for i in res["imported"]}
    with _conn() as c:
        for did in new.values():
            assert c.execute("SELECT owner_user_id FROM dramas WHERE id = ?", (did,)
                             ).fetchone()[0] == me
        # no series: private (share_by_default is off)
        assert c.execute("SELECT is_private FROM dramas WHERE id = ?", (new[g],)).fetchone()[0] == 1
        sid = c.execute("SELECT series_id FROM dramas WHERE id = ?", (new[a],)).fetchone()[0]
        assert sid not in live_series
        assert c.execute("SELECT owner_user_id, is_private FROM series WHERE id = ?", (sid,)
                         ).fetchone() == (me, 1)
        # a drama in a series follows the series
        assert c.execute("SELECT is_private FROM dramas WHERE id = ?", (new[a],)).fetchone()[0] == 0
        assert c.execute("SELECT notion_page_id FROM dramas WHERE id = ?", (new[a],)
                         ).fetchone()[0] is None


def test_share_by_default_makes_imports_shared(isolated_db):
    me = db.auth_create_user("me@example.com")
    db.auth_update_user(me, share_by_default=1)
    a, b, g = _world()
    res = bis.import_dramas(io.BytesIO(_manual_zip()), [g], confirm=True, confirm_text="RESTORE",
                            principal={"user_id": me, "is_local_owner": False})
    assert db.get_item_ownership("drama", res["imported"][0]["drama_id"])["is_private"] == 0


def test_local_owner_import_has_no_owner_and_ignores_file_owner(client):
    other = db.auth_create_user("other@example.com")
    a, b, g = _world(owner=other)
    r = _post_import(client, _manual_zip(), [g])
    new = r.json()["imported"][0]["drama_id"]
    row = [d for d in _dramas() if d[0] == new][0]
    assert row[2] is None and row[3] == 1


def test_schema_drift_tolerated(client):
    """A newer file has extra columns; an older one lacks some (they default)."""
    a, b, g = _world()
    dest = os.path.join(db.LIBRARY_DIR, "drift.db")
    las._sanitized_snapshot(dest)
    with contextlib.closing(sqlite3.connect(dest)) as c:
        c.execute("ALTER TABLE dramas ADD COLUMN from_the_future TEXT")
        c.execute("UPDATE dramas SET from_the_future = 'x'")
        c.execute("ALTER TABLE lines DROP COLUMN sfx")
        c.execute("DROP TABLE line_emotions")
        c.commit()
    with open(dest, "rb") as fh:
        data = fh.read()
    listed = _post_list(client, data, "library.db").json()
    assert listed["schema_differs"] is True and len(listed["dramas"]) == 3
    r = _post_import(client, data, [g])
    assert r.status_code == 200, r.text
    with _conn() as c:
        assert c.execute("SELECT COUNT(*) FROM lines WHERE drama_id = ?",
                         (r.json()["imported"][0]["drama_id"],)).fetchone()[0] == 3


# ---- rejected input --------------------------------------------------------

def test_wrong_confirmation_writes_nothing(client):
    a, b, g = _world()
    data = _manual_zip()
    before = _dramas()
    for form in ({"confirm": "false"}, {"confirm_text": "restore"}, {"confirm_text": ""},
                 {"confirm": ""}):
        assert _post_import(client, data, [g], **form).status_code == 422, form
    assert _dramas() == before


def test_missing_or_bad_ids(client):
    a, b, g = _world()
    data = _manual_zip()
    assert _post_import(client, data, [99999]).status_code == 404
    assert _post_import(client, data, [0]).status_code == 422
    assert client.post("/api/backups/import", files={"file": ("b.zip", data)}, data=OK
                       ).status_code == 422
    assert _post_import(client, data, list(range(1, 300))).status_code == 422
    assert len(_dramas()) == 3


@pytest.mark.parametrize("payload", [b"", b"not a zip at all", b"PK\x03\x04garbage"])
def test_bad_file_rejected(client, payload):
    _world()
    assert _post_list(client, payload).status_code == 422
    assert _post_import(client, payload, [1]).status_code == 422
    assert len(_dramas()) == 3


def test_zip_without_database_and_zip_slip_rejected(client):
    _world()
    assert _post_list(client, _fixture_zip(lambda z: z.writestr("readme.txt", "x"))
                      ).status_code == 422
    with open(db.DB_PATH, "rb") as fh:
        raw = fh.read()

    def slip(z):
        z.writestr("library.db", raw)
        z.writestr("../evil.txt", "x")
    r = _post_list(client, _fixture_zip(slip))
    assert r.status_code == 422 and "unsafe" in r.text
    assert not os.path.exists(os.path.join(os.path.dirname(db.LIBRARY_DIR), "evil.txt"))


def test_corrupt_database_rejected(client):
    _world()
    assert _post_list(client, b"SQLite format 3\x00" + os.urandom(4096), "library.db"
                      ).status_code == 422
    data = _fixture_zip(lambda z: z.writestr("library.db", b"SQLite format 3\x00" + b"\x01" * 5000))
    assert _post_list(client, data).status_code == 422


def test_oversize_upload_rejected(client, monkeypatch):
    _world()
    data = _manual_zip()
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "0.01")
    r = _post_list(client, data)
    assert r.status_code == 422 and "larger than the" in r.text
    assert len(_dramas()) == 3


def test_zip_bomb_limits(client, monkeypatch):
    _world()
    data = _manual_zip(media={1: "a.bin"})
    monkeypatch.setattr(bis, "_MAX_DB_BYTES", 1024)
    assert _post_list(client, data).status_code == 422
    monkeypatch.undo()
    monkeypatch.setattr(las, "RESTORE_MAX_MEMBERS", 1)
    assert _post_list(client, data).status_code == 422


def test_row_limit(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip()
    monkeypatch.setattr(bis, "_MAX_ROWS_PER_IMPORT", 5)
    assert _post_import(client, data, [a]).status_code == 422
    assert len(_dramas()) == 3


def test_refused_during_maintenance_or_running_export(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip()
    assert background_jobs.acquire_exclusive("restore")   # a whole-library restore
    try:
        assert _post_import(client, data, [g]).status_code == 409
    finally:
        background_jobs.release_exclusive()
    monkeypatch.setattr(las, "_job_id_running", lambda job_id: job_id == las.EXPORT_JOB_ID)
    assert _post_import(client, data, [g]).status_code == 409
    monkeypatch.undo()
    monkeypatch.setattr(abs_, "job_running", lambda: True)
    assert _post_import(client, data, [g]).status_code == 409
    assert len(_dramas()) == 3


def test_failure_rolls_back_everything(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip(media={a: "audio.mp3"})
    real = abs_.copy_drama
    calls = []

    def flaky(*args, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("boom /secret/path")
        return real(*args, **kw)
    monkeypatch.setattr(abs_, "copy_drama", flaky)
    before = _dramas()
    r = _post_import(client, data, [a, b])
    assert r.status_code >= 400 and "secret" not in r.text
    assert _dramas() == before
    with _conn() as c:
        assert c.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 1
    assert sorted(os.listdir(db.DRAMAS_DIR)) == [str(a)]


# ---- audit and permissions -------------------------------------------------

def test_audit_entry_ids_only(client):
    a, b, g = _world()
    r = _post_import(client, _manual_zip(), [g])
    new = r.json()["imported"][0]["drama_id"]
    with _conn() as c:
        rows = c.execute("SELECT detail_redacted FROM audit_log WHERE action = 'library.import_dramas'"
                         ).fetchall()
    assert len(rows) == 1 and str(new) in rows[0][0] and "Gamma" not in rows[0][0]


def _local_on():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _remote_on():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                      raise_server_exceptions=False)


def test_local_only_routes(isolated_db):
    seen = {}
    for _r, path, _m, decls in api_auth.iter_route_declarations(
            create_app(ApiSettings(auth_mode="on"))):
        if path.startswith("/api/backups/import"):
            seen[path] = decls
    assert seen == {"/api/backups/import": [("local_only", None)],
                    "/api/backups/import/list": [("local_only", None)]}


def test_remote_refused_and_local_owner_allowed(isolated_db):
    a, b, g = _world()
    data = _manual_zip()
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    headers = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
               api_auth.CSRF_HEADER: s["csrf_token"]}
    files = {"file": ("b.zip", data)}
    for h in ({}, headers):
        r = _remote_on().post("/api/backups/import/list", files=files, headers=h)
        assert r.status_code in (401, 403)
        r = _remote_on().post("/api/backups/import", files=files, headers=h,
                              data={"drama_ids": [str(g)], **OK})
        assert r.status_code in (401, 403)
    assert len(_dramas()) == 3
    assert _local_on().post("/api/backups/import/list", files=files).status_code == 200
    r = _local_on().post("/api/backups/import", files=files, data={"drama_ids": [str(g)], **OK})
    assert r.status_code == 200 and len(_dramas()) == 4


# ---- coverage --------------------------------------------------------------

# Columns the import copies as they are. A new dramas/lines/characters column
# fails test_every_column_is_imported_or_explicitly_ignored until it is listed
# here (copied automatically) or in bis.FORCED_COLUMNS / bis.IGNORED_COLUMNS.
COPIED = {
    "dramas": {"title_zh", "title_en", "author", "studio", "director", "voice_actors", "summary",
               "status", "content_mode", "narration_language", "source_language", "media_type",
               "episode_number", "episode_summary",
               "translation_engine", "author_romanized", "studio_romanized",
               "voice_actors_romanized", "director_romanized", "genre",
               "publication_status", "chapter_count", "custom_tags", "last_translate_errors",
               "personal_notes", "created_at", "chinese_script",
               "source_url", "transcript_mode", "whisper_size", "alignment_method",
               "asr_backend_choice", "min_silence_ms", "vad_threshold", "beam_size", "hallucination_silence_sec", "min_pause_sec",
               "separate_vocals_first", "separation_backend", "realign_long_segments",
               "whisper_fast_mode", "use_groq", "hardsub_ocr_backend", "hardsub_interval_sec",
               "project_instructions", "reading_speed_mode"},
    "lines": {"idx", "start", "end", "zh", "en", "speaker", "flag", "flag_note",
              "speaker_manual", "sfx", "lang", "word_timings"},
    "characters": {"speaker_label", "character_name", "voice_actor", "tts_voice", "offline_voice",
                   "ref_text", "elevenlabs_voice_id", "clone_engine",
                   "voice_design", "pronouns"},
}


def test_every_column_is_imported_or_explicitly_ignored(isolated_db):
    with _conn() as c:
        live = {t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')] for t in COPIED}
    for table, cols in live.items():
        known = (COPIED[table] | bis.FORCED_COLUMNS[table] | bis.IGNORED_COLUMNS[table]
                 | bis.FILE_COLUMNS[table])
        assert set(cols) == known, (table, set(cols) ^ known)
    with _conn() as c:
        pages = {r[1] for r in c.execute('PRAGMA table_info("pages")')}
    assert {"filename", "rendered_filename"} <= pages and bis.FILE_COLUMNS["pages"] == {
        "filename", "rendered_filename"}


def test_copied_columns_really_arrive(isolated_db):
    """Fills every copied column with a value and checks it survives an import."""
    with _conn() as c:
        types = {t: {r[1]: (r[2] or "").upper() for r in c.execute(f'PRAGMA table_info("{t}")')}
                 for t in COPIED}

        def value(t, col):
            typ = types[t][col]
            return 7 if "INT" in typ else 7.5 if typ in ("REAL", "FLOAT") else f"{col}-v"
        did = _ins(c, "dramas", **{col: value("dramas", col) for col in COPIED["dramas"]})
        _ins(c, "lines", drama_id=did, **{col: value("lines", col) for col in COPIED["lines"]})
        _ins(c, "characters", drama_id=did,
             **{col: value("characters", col) for col in COPIED["characters"]})
        c.commit()
    res = bis.import_dramas(io.BytesIO(_manual_zip()), [did], confirm=True, confirm_text="RESTORE")
    new = res["imported"][0]["drama_id"]
    with _conn() as c:
        for table, key in (("dramas", "id"), ("lines", "drama_id"), ("characters", "drama_id")):
            skip = {"title_en", "title_zh"} if table == "dramas" else set()
            q = ", ".join(f'"{x}"' for x in sorted(COPIED[table] - skip))
            old = c.execute(f'SELECT {q} FROM "{table}" WHERE {key} = ?', (did,)).fetchone()
            newer = c.execute(f'SELECT {q} FROM "{table}" WHERE {key} = ?', (new,)).fetchone()
            assert old == newer, table


# ---- file references ---------------------------------------------------------

_FILE_REFS = {"dramas": ("audio_filename", "source_video_filename", "novel_reference_filename",
                         "cover_art_filename"),
              "lines": ("dub_filename",), "characters": ("ref_audio_filename",),
              "pages": ("filename", "rendered_filename")}


def _set_refs(did, value):
    """Every file-reference column of drama `did` (and its line, character
    and a new page) set to `value`; returns the page id."""
    with _conn() as c:
        for col in _FILE_REFS["dramas"]:
            c.execute(f'UPDATE dramas SET "{col}" = ? WHERE id = ?', (value, did))
        c.execute("UPDATE lines SET dub_filename = ? WHERE drama_id = ?", (value, did))
        c.execute("UPDATE characters SET ref_audio_filename = ? WHERE drama_id = ?", (value, did))
        pid = _ins(c, "pages", drama_id=did, idx=0, filename=value, rendered_filename=value)
        c.commit()
    return pid


def _refs(did):
    with _conn() as c:
        out = {}
        for table, cols in _FILE_REFS.items():
            key = "id" if table == "dramas" else "drama_id"
            for col in cols:
                out[(table, col)] = {r[0] for r in c.execute(
                    f'SELECT "{col}" FROM "{table}" WHERE {key} = ?', (did,))}
        return out


@pytest.mark.parametrize("hostile", ["C:\\Users\\x\\a.txt", "/etc/passwd", "../5/novel_reference.txt",
                                     "a/../../b", "pages/../../x.png", "..", "a\x00b.png",
                                     "pages/sub/x.png", "C:x.png", "pages//x.png"])
def test_hostile_file_references_are_cleared(client, hostile):
    a, b, g = _world()
    _set_refs(a, hostile)
    data = _manual_zip(media={a: "audio.mp3"})    # the drama's files ARE imported
    r = _post_import(client, data, [a])
    assert r.status_code == 200, r.text
    assert r.json()["imported"][0]["media_imported"] is True
    new = r.json()["imported"][0]["drama_id"]
    assert all(v == {None} for v in _refs(new).values()), _refs(new)
    detail = client.get(f"/api/library/dramas/{new}").json()
    assert not detail["has_audio"] and not detail["has_cover_art"]
    assert not detail["has_novel_reference"]


def _legit_media(did):
    d = os.path.join(db.DRAMAS_DIR, str(did))
    for rel in ("audio.mp3", "cover.jpg", "pages/p1.png", "dub_clips/line_0001.wav",
                "voice_refs/r.wav"):
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with open(os.path.join(d, rel), "wb") as fh:
            fh.write(b"x")
    with _conn() as c:
        c.execute("UPDATE dramas SET audio_filename = 'audio.mp3', cover_art_filename = 'cover.jpg',"
                  " novel_reference_filename = 'novel_reference.txt' WHERE id = ?", (did,))
        c.execute("UPDATE lines SET dub_filename = 'dub_clips\\line_0001.wav' WHERE drama_id = ?",
                  (did,))
        c.execute("UPDATE characters SET ref_audio_filename = 'voice_refs/r.wav' WHERE drama_id = ?",
                  (did,))
        _ins(c, "pages", drama_id=did, idx=0, filename="pages/p1.png", rendered_filename="p1.png")
        c.commit()


def test_zip_with_media_keeps_plain_file_names(client):
    a, b, g = _world()
    _legit_media(a)
    r = _post_import(client, _manual_zip(), [a])
    assert r.status_code == 200, r.text
    new = r.json()["imported"][0]["drama_id"]
    refs = _refs(new)
    assert refs[("dramas", "audio_filename")] == {"audio.mp3"}
    assert refs[("dramas", "cover_art_filename")] == {"cover.jpg"}
    assert refs[("dramas", "novel_reference_filename")] == {"novel_reference.txt"}
    assert refs[("dramas", "source_video_filename")] == {None}
    assert refs[("lines", "dub_filename")] == {"dub_clips/line_0001.wav"}
    assert refs[("characters", "ref_audio_filename")] == {"voice_refs/r.wav"}
    assert refs[("pages", "filename")] == {"pages/p1.png"}
    assert refs[("pages", "rendered_filename")] == {"p1.png"}
    assert os.path.isfile(os.path.join(db.DRAMAS_DIR, str(new), "pages", "p1.png"))
    detail = client.get(f"/api/library/dramas/{new}").json()
    assert detail["has_audio"] and detail["has_cover_art"]


def test_database_only_import_clears_file_references(client):
    a, b, g = _world()
    _legit_media(a)
    dest = os.path.join(db.LIBRARY_DIR, "x.db")
    las._sanitized_snapshot(dest)
    with open(dest, "rb") as fh:
        r = _post_import(client, fh.read(), [a])
    assert r.status_code == 200, r.text
    new = r.json()["imported"][0]["drama_id"]
    assert all(v == {None} for v in _refs(new).values()), _refs(new)
    detail = client.get(f"/api/library/dramas/{new}").json()
    assert not detail["has_audio"] and not detail["has_cover_art"]
    assert not detail["has_novel_reference"]


def test_zip_without_this_dramas_media_clears_file_references(client):
    a, b, g = _world()
    _legit_media(a)
    _set_refs(g, "audio.mp3")                       # g has no files in the zip
    r = _post_import(client, _manual_zip(), [g])
    new = r.json()["imported"][0]["drama_id"]
    assert all(v == {None} for v in _refs(new).values()), _refs(new)


# ---- hostile databases -------------------------------------------------------

def _db_bytes(mutate) -> bytes:
    dest = os.path.join(db.LIBRARY_DIR, "hostile.db")
    las._sanitized_snapshot(dest)
    with contextlib.closing(sqlite3.connect(dest)) as c:
        mutate(c)
        c.commit()
    with open(dest, "rb") as fh:
        data = fh.read()
    os.remove(dest)
    return data


_ENDLESS = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
            "SELECT i AS drama_id FROM n")


@pytest.mark.parametrize("table", ["lines", "characters", "glossary_terms", "bubbles"])
def test_view_in_place_of_a_table_is_rejected(client, table):
    a, b, g = _world()

    def swap(c):
        c.execute(f'DROP TABLE "{table}"')
        c.execute(f'CREATE VIEW "{table}" AS {_ENDLESS}')
    data = _db_bytes(swap)
    before = _dramas()
    assert _post_list(client, data, "library.db").status_code == 422
    assert _post_import(client, data, [a]).status_code == 422
    assert _dramas() == before


def test_differently_cased_or_virtual_table_is_rejected(client):
    a, b, g = _world()

    def rename(c):
        c.execute('ALTER TABLE lines RENAME TO "Lines_tmp"')
        c.execute('ALTER TABLE "Lines_tmp" RENAME TO "LINES"')
    assert _post_list(client, _db_bytes(rename), "library.db").status_code == 422

    def virtual(c):
        c.execute("DROP TABLE bubbles")
        c.execute("CREATE VIRTUAL TABLE bubbles USING fts5(page_id)")
    try:
        data = _db_bytes(virtual)
    except sqlite3.OperationalError:
        pytest.skip("this SQLite has no fts5")
    assert _post_list(client, data, "library.db").status_code == 422


def test_reads_of_the_file_stop_at_the_deadline(isolated_db, monkeypatch):
    monkeypatch.setattr(bis, "_READ_TIME_LIMIT_S", 0.2)
    with contextlib.closing(bis._open_db(db.DB_PATH)) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(_ENDLESS + " WHERE i < 0").fetchall()


def test_media_ids_accept_only_canonical_digits(isolated_db):
    backup = bis._Backup.__new__(bis._Backup)
    backup.zip_path = os.path.join(db.LIBRARY_DIR, "m.zip")
    with zipfile.ZipFile(backup.zip_path, "w") as zf:
        for name in ("dramas/7/a.mp3", "dramas/07/a.mp3", "dramas/\u00b2/a.mp3",
                     "dramas/\u0663/a.mp3", "dramas/+8/a.mp3", "dramas/9/", "dramas/ 10/a.mp3",
                     "dramas\\11\\a.mp3"):
            zf.writestr(name, "x")
    assert backup.media_ids() == {7, 11}
    assert bis._plain_id("12") and not bis._plain_id("012") and not bis._plain_id("\u00b2")


def test_row_limit_counts_series_tables_and_bubbles(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip()
    monkeypatch.setattr(bis, "_MAX_ROWS_PER_IMPORT", 10)
    assert _post_import(client, data, [g]).status_code == 200     # 6 rows
    with _conn() as c:
        sid = c.execute("SELECT series_id FROM dramas WHERE id = ?", (a,)).fetchone()[0]
        for i in range(10):
            _ins(c, "glossary_terms", series_id=sid, term_original=f"t{i}", term_translation="x")
        pid = _ins(c, "pages", drama_id=g, idx=0)
        for i in range(10):
            _ins(c, "bubbles", page_id=pid, idx=i)
        c.commit()
    data = _manual_zip()
    for did in (a, g):
        r = _post_import(client, data, [did])
        assert r.status_code == 422 and "too many rows" in r.text, did


def test_upload_cap_applies_before_the_body_is_read(client, monkeypatch):
    _world()
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "0.01")
    called = []
    monkeypatch.setattr(bis, "list_backup_dramas", lambda *a, **k: called.append(1))
    monkeypatch.setattr(bis, "import_dramas", lambda *a, **k: called.append(1))
    big = b"PK" + os.urandom(200 * 1024)
    assert _post_list(client, big).status_code == 413
    assert _post_import(client, big, [1]).status_code == 413
    assert not called


def test_form_fields_are_checked(client):
    a, b, g = _world()
    data = _manual_zip()
    for bad in ("x", "-1", "1.5", "\u00b2", "99999999999"):
        assert _post_import(client, data, [bad]).status_code == 422, bad
    assert client.post("/api/backups/import/list", data={"file": "not a file"}).status_code == 422
    assert len(_dramas()) == 3


def test_zip_database_gets_no_unguarded_sql(client):
    """A zip's library.db is only queried on the guarded connection: a view
    named dramas that never returns a row is refused at once, and the
    library is not left locked."""
    a, b, g = _world()

    def swap(c):
        c.execute("DROP TABLE dramas")
        c.execute("CREATE VIEW dramas AS WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL "
                  "SELECT i + 1 FROM n) SELECT i AS id, '' AS title_en, '' AS title_zh, "
                  "'' AS media_type FROM n WHERE i < 0")
    raw = _db_bytes(swap)
    data = _fixture_zip(lambda z: z.writestr("library.db", raw))
    started = time.monotonic()
    assert _post_list(client, data).status_code == 422
    assert _post_import(client, data, [a]).status_code == 422
    assert time.monotonic() - started < 10
    assert not background_jobs.maintenance_active()
    assert background_jobs.acquire_exclusive("restore")
    background_jobs.release_exclusive()


def test_saved_versions_dub_filenames_are_sanitised(client):
    a, b, g = _world()
    _legit_media(a)
    items = [{"id": 1, "en": "x", "dub_filename": "/etc/passwd"},
             {"id": 2, "en": "y", "dub_filename": "dub_clips/line_0001.wav"},
             {"id": 3, "en": "z", "dub_filename": "../1/dub_clips/line_0001.wav"}]
    with _conn() as c:
        _ins(c, "translation_versions", drama_id=a, label="v", lines_json=json.dumps(items),
             created_at="x")
        _ins(c, "line_history", drama_id=a, label="h", snapshot_json=json.dumps(items),
             created_at="x")
        _ins(c, "line_history", drama_id=a, label="odd",
             snapshot_json=json.dumps({"dub_filename": "/etc/passwd"}), created_at="x")
        c.commit()
    for data in (_manual_zip(), _db_bytes(lambda c: None)):
        r = _post_import(client, data, [a])
        assert r.status_code == 200, r.text
        new = r.json()["imported"][0]["drama_id"]
        kept = "dub_clips/line_0001.wav" if r.json()["imported"][0]["media_imported"] else None
        with _conn() as c:
            [v] = c.execute("SELECT lines_json FROM translation_versions WHERE drama_id = ?",
                            (new,)).fetchall()
            hist = dict(c.execute("SELECT label, snapshot_json FROM line_history "
                                  "WHERE drama_id = ?", (new,)).fetchall())
        for value in (v[0], hist["h"]):
            assert [i["dub_filename"] for i in json.loads(value)] == [None, kept, None]
        assert json.loads(hist["odd"]) == []


def test_value_and_total_byte_caps(client, monkeypatch):
    a, b, g = _world()
    with _conn() as c:
        _ins(c, "line_history", drama_id=g, label="big", snapshot_json="[" + " " * 5000 + "]",
             created_at="x")
        c.commit()
    data = _manual_zip()
    monkeypatch.setattr(bis, "_MAX_VALUE_BYTES", 4096)
    r = _post_import(client, data, [g])
    assert r.status_code == 422 and "too much data" in r.text
    monkeypatch.setattr(bis, "_MAX_VALUE_BYTES", 64 * 1024 ** 2)
    monkeypatch.setattr(bis, "_MAX_IMPORT_BYTES", 4096)
    assert _post_import(client, data, [g]).status_code == 422
    assert len(_dramas()) == 3
    monkeypatch.undo()
    assert _post_import(client, data, [g]).status_code == 200


# ---- media folders: the commit is the commit point -------------------------

def _entries():
    return sorted(os.listdir(db.DRAMAS_DIR))


def _stagings():
    return [n for n in _entries() if n.startswith(db.MEDIA_STAGING_PREFIX)]


def _folder_with(path, name, content):
    os.makedirs(path)
    with open(os.path.join(path, name), "wb") as fh:
        fh.write(content)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _fail_commit_after_move(monkeypatch):
    """The commit right after the staged folders are moved into place
    raises; returns the dramas/ entries seen just after the move."""
    seen, armed = [], []
    real_move, real_commit = abs_.move_media_in, sqlite3.Connection.commit

    def move(*args, **kw):
        real_move(*args, **kw)
        seen.extend(_entries())
        armed.append(1)

    def commit(self):
        if armed:
            armed.clear()
            raise sqlite3.OperationalError("disk I/O error")
        return real_commit(self)
    monkeypatch.setattr(abs_, "move_media_in", move)
    monkeypatch.setattr(db._TrackedConnection, "commit", commit, raising=False)
    return seen


def _die_without_cleanup(monkeypatch):
    """The process 'dies' where the import would clean up its staging."""
    monkeypatch.setattr(abs_, "end_media_staging", lambda staging: True)


def _restart(monkeypatch):
    monkeypatch.undo()
    # A new process has no running imports, and a dead one holds no locks.
    for entry in db._active_media_stagings.values():
        db._release_lock(entry["lock"])
    db._active_media_stagings.clear()


def test_commit_failure_removes_only_this_imports_folders(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip(media={a: "a.mp3", b: "b.mp3"})
    _folder_with(os.path.join(db.DRAMAS_DIR, "777"), "keep.txt", b"keep")
    seen = _fail_commit_after_move(monkeypatch)
    before = _dramas()
    r = _post_import(client, data, [a, b])
    assert r.status_code == 500 and "nothing was changed" in r.text
    assert {str(g + 1), str(g + 2)} <= set(seen)     # they were in place before the commit
    assert _dramas() == before
    assert _entries() == sorted([str(a), str(b), "777"])
    assert _read(os.path.join(db.DRAMAS_DIR, str(a), "a.mp3")) == b"media-a.mp3"
    assert _read(os.path.join(db.DRAMAS_DIR, "777", "keep.txt")) == b"keep"


def test_failed_removal_is_reported_and_retried_at_startup(client, monkeypatch, caplog):
    a, b, g = _world()
    data = _manual_zip(media={a: "a.mp3"})
    _fail_commit_after_move(monkeypatch)
    final = os.path.join(db.DRAMAS_DIR, str(g + 1))
    real_rmtree, real_rename = shutil.rmtree, os.rename

    def rmtree(path, *args, **kw):
        if os.path.abspath(path) == os.path.abspath(final):
            raise PermissionError("in use")
        return real_rmtree(path, *args, **kw)

    def rename(src, dst, *args, **kw):
        if os.path.abspath(src) == os.path.abspath(final):
            raise PermissionError("in use")     # the rename back fails too
        return real_rename(src, dst, *args, **kw)
    monkeypatch.setattr(shutil, "rmtree", rmtree)
    monkeypatch.setattr(os, "rename", rename)
    before = _dramas()
    with caplog.at_level(logging.WARNING, logger="db"):
        r = _post_import(client, data, [a])
    assert r.status_code == 500 and "could not be cleaned up" in r.text
    _no_leak(r)
    assert str(g + 1) in caplog.text and db.LIBRARY_DIR not in caplog.text
    assert _dramas() == before and os.path.isdir(final)
    [left] = _stagings()
    assert os.path.isfile(os.path.join(db.DRAMAS_DIR, left, "journal.json"))
    _restart(monkeypatch)
    assert abs_.cleanup_stale_leftovers() == 1
    assert _entries() == [str(a)]


def _crashed_import(client, monkeypatch, media_of):
    """An import of every drama in media_of whose process died between the
    move and the commit; returns (world ids, staging path) after a restart."""
    ids = _world()
    data = _manual_zip(media={ids[i]: name for i, name in media_of.items()})
    before = set(_stagings())
    _fail_commit_after_move(monkeypatch)
    _die_without_cleanup(monkeypatch)
    assert _post_import(client, data, [ids[i] for i in media_of]).status_code == 500
    [staging] = set(_stagings()) - before
    _restart(monkeypatch)
    return ids, os.path.join(db.DRAMAS_DIR, staging)


def test_crash_between_move_and_commit_is_recovered_at_startup(client, monkeypatch):
    _folder_with(os.path.join(db.DRAMAS_DIR, "777"), "keep.txt", b"keep")
    lookalike = os.path.join(db.DRAMAS_DIR, ".import-notours")
    _folder_with(lookalike, "x", b"x")
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3", 1: "b.mp3"})
    new_a, new_b = (os.path.join(db.DRAMAS_DIR, str(i)) for i in (g + 1, g + 2))
    assert os.path.isdir(new_a) and os.path.isdir(new_b)
    assert abs_.cleanup_stale_leftovers() == 1
    assert _entries() == sorted([str(a), str(b), "777", ".import-notours"])
    assert _read(os.path.join(db.DRAMAS_DIR, "777", "keep.txt")) == b"keep"
    assert _read(os.path.join(db.DRAMAS_DIR, str(a), "a.mp3")) == b"media-a.mp3"
    assert _read(os.path.join(lookalike, "x")) == b"x"


def test_a_folder_that_replaced_a_moved_one_is_kept_and_reported(client, monkeypatch, caplog):
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3", 1: "b.mp3"})
    new_a, new_b = (os.path.join(db.DRAMAS_DIR, str(i)) for i in (g + 1, g + 2))
    shutil.rmtree(new_b)
    _folder_with(new_b, "theirs.txt", b"theirs")
    with caplog.at_level(logging.WARNING, logger="db"):
        assert db.recover_media_imports() == {"settled": 0, "failed_ids": [g + 2]}
    assert str(g + 2) in caplog.text and db.LIBRARY_DIR not in caplog.text
    assert not os.path.lexists(new_a)       # provably this import's: removed
    assert _read(os.path.join(new_b, "theirs.txt")) == b"theirs"
    assert os.path.isfile(os.path.join(staging, "journal.json"))     # kept for a retry
    assert db.recover_media_imports() == {"settled": 0, "failed_ids": [g + 2]}
    assert _read(os.path.join(new_b, "theirs.txt")) == b"theirs"


def test_a_replacement_folder_with_a_reused_inode_is_kept(client, monkeypatch):
    # Inode numbers are reused: a folder someone put in the moved folder's place
    # can report the very identity the journal recorded. Without the import's
    # marker it must still be kept, never removed.
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3", 1: "b.mp3"})
    new_b = os.path.join(db.DRAMAS_DIR, str(g + 2))
    recorded = db._folder_identity(new_b)
    shutil.rmtree(new_b)
    _folder_with(new_b, "theirs.txt", b"theirs")
    monkeypatch.setattr(db, "_folder_identity", lambda path: recorded)
    assert db.recover_media_imports() == {"settled": 0, "failed_ids": [g + 2]}
    assert _read(os.path.join(new_b, "theirs.txt")) == b"theirs"


def test_recovery_proves_a_folder_by_its_marker_when_the_inode_changed(client, monkeypatch):
    # FAT/exFAT/SMB: the rename gave the folder another inode number, or none.
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3", 1: "b.mp3"})
    monkeypatch.setattr(db, "_folder_identity", lambda path: None)
    assert db.recover_media_imports() == {"settled": 1, "failed_ids": []}
    assert _entries() == sorted([str(a), str(b)])


def test_recovery_with_a_changed_inode_and_no_marker_keeps_the_folder(client, monkeypatch):
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3"})
    new_a = os.path.join(db.DRAMAS_DIR, str(g + 1))
    os.remove(os.path.join(new_a, db.MEDIA_MARKER))
    for identity in (None, [1, 2]):
        monkeypatch.setattr(db, "_folder_identity", lambda path, i=identity: i)
        assert db.recover_media_imports() == {"settled": 0, "failed_ids": [g + 1]}
        assert _read(os.path.join(new_a, "a.mp3")) == b"media-a.mp3"
    # The next new drama with that id doesn't inherit it: it is renamed aside, kept.
    assert db.create_drama(title_en="Fresh") == g + 1
    [aside] = [n for n in _entries() if n.startswith(f"{g + 1}.orphan-")]
    assert _read(os.path.join(db.DRAMAS_DIR, aside, "a.mp3")) == b"media-a.mp3"


def test_commit_failure_cleans_up_on_a_file_system_without_inode_numbers(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip(media={a: "a.mp3", b: "b.mp3"})
    monkeypatch.setattr(db, "_folder_identity", lambda path: None)
    _fail_commit_after_move(monkeypatch)
    before = _dramas()
    r = _post_import(client, data, [a, b])
    assert r.status_code == 500 and "nothing was changed" in r.text
    assert _dramas() == before and _entries() == sorted([str(a), str(b)])
    assert db.create_drama(title_en="Fresh") == g + 1
    assert not os.path.lexists(os.path.join(db.DRAMAS_DIR, str(g + 1)))


def test_commit_failure_renames_back_even_when_nothing_proves_the_folder(client, monkeypatch):
    # The failing import itself knows exactly which folders it moved.
    a, b, g = _world()
    data = _manual_zip(media={a: "a.mp3"})
    monkeypatch.setattr(db, "_folder_identity", lambda path: None)
    monkeypatch.setattr(db, "_marker_matches", lambda folder, token: False)
    _fail_commit_after_move(monkeypatch)
    r = _post_import(client, data, [a])
    # Unprovable: kept in place and reported, not deleted.
    assert r.status_code == 500 and "could not be cleaned up" in r.text
    assert _read(os.path.join(db.DRAMAS_DIR, str(g + 1), "a.mp3")) == b"media-a.mp3"
    assert len(_stagings()) == 1


def test_recovery_skips_a_staging_folder_another_import_holds(client, monkeypatch):
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3"})
    holder = [db._lock_file(os.path.join(staging, "lock"), False)]
    assert holder[0] is not None
    try:
        assert db.recover_media_imports() == {"settled": 0, "failed_ids": []}
        assert os.path.isdir(os.path.join(db.DRAMAS_DIR, str(g + 1)))
    finally:
        db._release_lock(holder)
    assert db.recover_media_imports()["settled"] == 1
    assert _entries() == [str(a)]


def test_a_young_staging_folder_without_journal_is_kept_until_stale(client):
    staging = os.path.join(db.DRAMAS_DIR, db.MEDIA_STAGING_PREFIX + "ab" * 16)
    _folder_with(os.path.join(staging, "drama-1"), "a.mp3", b"a")
    assert db.recover_media_imports() == {"settled": 0, "failed_ids": []}
    assert os.path.isdir(staging)
    assert db.recover_media_imports(now=time.time() + 2 * 86400)["settled"] == 1
    assert not os.path.lexists(staging)


@pytest.mark.parametrize("journal", [
    b"[" * 200000,
    b"\xff\xfe",
    b'{"format": 2, "staging": "%s", "ids": {"99999999999999999999999": '
    b'{"marker": "' + b"a" * 32 + b'", "ident": null}}}',
    b'{"format": 2, "staging": "%s", "ids": {"' + b"1" * 5000 + b'": {"marker": "'
    + b"a" * 32 + b'", "ident": null}}}',
    b'{"format": 2, "staging": "%s", "ids": {"%d": {"marker": "x", "ident": null}}}',
    b'{"format": 2, "staging": "%s", "ids": {"%d": {"marker": "' + b"a" * 32
    + b'", "ident": [1e999, 2]}}}',
    b'{"format": 2, "staging": ".import-' + b"0" * 32 + b'", "ids": {}}',
    b'{"format": 1, "ids": {"%d": null}}',
    b"[]",
])
def test_a_damaged_journal_leaves_everything_alone(client, monkeypatch, journal):
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3"})
    name = os.path.basename(staging).encode()
    text = journal.replace(b"%s", name).replace(b"%d", str(g + 1).encode())
    with open(os.path.join(staging, "journal.json"), "wb") as fh:
        fh.write(text)
    assert db.recover_media_imports() == {"settled": 0, "failed_ids": []}
    assert _read(os.path.join(db.DRAMAS_DIR, str(g + 1), "a.mp3")) == b"media-a.mp3"
    assert os.path.isdir(staging)


def test_an_unreadable_library_database_leaves_everything_alone(client, monkeypatch):
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3"})
    new_a = os.path.join(db.DRAMAS_DIR, str(g + 1))
    empty = os.path.join(db.LIBRARY_DIR, "empty.db")
    sqlite3.connect(empty).close()
    for path in (os.path.join(db.LIBRARY_DIR, "missing.db"), empty):
        monkeypatch.setattr(db, "DB_PATH", path)
        assert db.recover_media_imports() == {"settled": 0, "failed_ids": []}
        assert os.path.isdir(new_a) and os.path.isdir(staging)
    monkeypatch.undo()
    assert db.recover_media_imports()["settled"] == 1
    assert not os.path.lexists(new_a)


def test_a_windows_junction_counts_as_a_link():
    import stat
    import types
    junction = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400)
    plain = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x10)
    assert db._is_link(junction) and not db._is_link(plain)


def test_a_stray_folder_for_a_new_id_is_renamed_aside(client):
    a, b, g = _world()
    data = _manual_zip(media={a: "a.mp3", b: "b.mp3"})
    taken = os.path.join(db.DRAMAS_DIR, str(g + 2))
    _folder_with(taken, "mine.txt", b"mine")
    r = _post_import(client, data, [a, b])
    assert r.status_code == 200, r.text
    assert [d["drama_id"] for d in r.json()["imported"]] == [g + 1, g + 2]
    [aside] = [n for n in _entries() if ".orphan-" in n]
    assert aside.startswith(f"{g + 2}.orphan-")
    assert _read(os.path.join(db.DRAMAS_DIR, aside, "mine.txt")) == b"mine"
    assert _read(os.path.join(taken, "b.mp3")) == b"media-b.mp3"
    assert not os.path.exists(os.path.join(taken, db.MEDIA_MARKER)) and not _stagings()
    # A drama imported without files can't inherit a stray folder either.
    stray = os.path.join(db.DRAMAS_DIR, str(g + 3))
    _folder_with(stray, "mine.txt", b"mine")
    assert _post_import(client, _manual_zip(), [g]).status_code == 200
    assert not os.path.lexists(stray)
    assert len([n for n in _entries() if n.startswith(f"{g + 3}.orphan-")]) == 1


def test_a_stray_folder_that_cannot_be_renamed_aside_aborts_the_import(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip(media={a: "a.mp3", b: "b.mp3"})
    taken = os.path.join(db.DRAMAS_DIR, str(g + 2))
    _folder_with(taken, "mine.txt", b"mine")
    real_rename = os.rename

    def rename(src, dst, *args, **kw):
        if os.path.abspath(src) == os.path.abspath(taken):
            raise PermissionError("in use")
        return real_rename(src, dst, *args, **kw)
    monkeypatch.setattr(os, "rename", rename)
    before = _dramas()
    r = _post_import(client, data, [a, b])
    assert r.status_code == 409 and "nothing was imported" in r.text
    _no_leak(r)
    assert _dramas() == before
    assert _entries() == sorted([str(a), str(b), str(g + 2)])   # g + 1 never moved in
    assert _read(os.path.join(taken, "mine.txt")) == b"mine"


def test_journal_of_a_committed_import_is_only_deleted(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip(media={a: "a.mp3"})
    _die_without_cleanup(monkeypatch)
    r = _post_import(client, data, [a])
    assert r.status_code == 200, r.text
    new = r.json()["imported"][0]["drama_id"]
    [left] = _stagings()
    assert os.path.isfile(os.path.join(db.DRAMAS_DIR, left, "journal.json"))
    _restart(monkeypatch)
    assert abs_.cleanup_stale_leftovers() == 1
    assert not _stagings() and db.get_drama(new) is not None
    assert _read(os.path.join(db.DRAMAS_DIR, str(new), "a.mp3")) == b"media-a.mp3"
    assert not os.path.exists(os.path.join(db.DRAMAS_DIR, str(new), db.MEDIA_MARKER))


def test_new_drama_never_inherits_a_stale_folder(client, monkeypatch):
    (a, b, g), staging = _crashed_import(client, monkeypatch, {0: "a.mp3"})
    orphan = os.path.join(db.DRAMAS_DIR, str(g + 1))
    assert os.path.isdir(orphan)
    # No startup sweep ran: the next new drama gets the orphan's id, and the
    # journalled orphan is cleared rather than inherited.
    assert db.create_drama(title_en="Fresh") == g + 1
    assert not os.path.lexists(orphan) and not _stagings()
    # A folder no import journal lists is renamed aside and kept.
    stray = os.path.join(db.DRAMAS_DIR, str(g + 2))
    _folder_with(stray, "mine.txt", b"mine")
    assert db.create_drama(title_en="Next") == g + 2
    assert not os.path.lexists(stray)
    [aside] = [n for n in _entries() if n.startswith(f"{g + 2}.orphan-")]
    assert _read(os.path.join(db.DRAMAS_DIR, aside, "mine.txt")) == b"mine"
    # One that can't be renamed aside refuses the create, and nothing is created.
    stray = os.path.join(db.DRAMAS_DIR, str(g + 3))
    _folder_with(stray, "mine.txt", b"mine")
    real_rename = os.rename

    def rename(src, dst, *args, **kw):
        if os.path.abspath(src) == os.path.abspath(stray):
            raise PermissionError("in use")
        return real_rename(src, dst, *args, **kw)
    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(db.DramaFolderConflict) as e:
        db.create_drama(title_en="Later")
    assert db.LIBRARY_DIR not in str(e.value)
    r = client.post("/api/dramas", json={"source_language": "zh", "title_en": "Later"})
    assert r.status_code == 409 and "move it out" in r.text
    _no_leak(r)
    assert [d[1] for d in _dramas()][-1] == "Next"
    assert _read(os.path.join(stray, "mine.txt")) == b"mine"
