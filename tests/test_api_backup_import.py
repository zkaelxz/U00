"""Import chosen dramas from a backup file (services/backup_import_service.py,
POST /api/backups/import/list and /import). No network, no models."""

import contextlib
import io
import os
import sqlite3
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
    assert r.status_code == 422 and "too large" in r.text
    assert len(_dramas()) == 3


def test_zip_bomb_limits(client, monkeypatch):
    _world()
    data = _manual_zip(media={1: "a.bin"})
    monkeypatch.setattr(bis, "_MAX_DB_BYTES", 1024)
    assert _post_list(client, data).status_code == 422
    monkeypatch.undo()
    monkeypatch.setattr(las, "_RESTORE_MAX_MEMBERS", 1)
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
    monkeypatch.setattr(abs_, "_job_running", lambda: True)
    assert _post_import(client, data, [g]).status_code == 409
    assert len(_dramas()) == 3


def test_failure_rolls_back_everything(client, monkeypatch):
    a, b, g = _world()
    data = _manual_zip(media={a: "audio.mp3"})
    real = abs_._copy_drama
    calls = []

    def flaky(*args, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("boom /secret/path")
        return real(*args, **kw)
    monkeypatch.setattr(abs_, "_copy_drama", flaky)
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
               "episode_number", "episode_summary", "audio_filename", "novel_reference_filename",
               "translation_engine", "author_romanized", "studio_romanized",
               "voice_actors_romanized", "director_romanized", "cover_art_filename", "genre",
               "publication_status", "chapter_count", "custom_tags", "last_translate_errors",
               "personal_notes", "created_at", "source_video_filename", "chinese_script",
               "source_url", "transcript_mode", "whisper_size", "alignment_method",
               "asr_backend_choice", "min_silence_ms", "vad_threshold", "beam_size",
               "separate_vocals_first", "separation_backend", "realign_long_segments",
               "whisper_fast_mode", "use_groq", "hardsub_ocr_backend", "hardsub_interval_sec",
               "project_instructions"},
    "lines": {"idx", "start", "end", "zh", "en", "speaker", "dub_filename", "flag", "flag_note",
              "speaker_manual", "sfx"},
    "characters": {"speaker_label", "character_name", "voice_actor", "tts_voice", "offline_voice",
                   "ref_audio_filename", "ref_text", "elevenlabs_voice_id", "clone_engine",
                   "voice_design", "pronouns"},
}


def test_every_column_is_imported_or_explicitly_ignored(isolated_db):
    with _conn() as c:
        live = {t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')] for t in COPIED}
    for table, cols in live.items():
        known = COPIED[table] | bis.FORCED_COLUMNS[table] | bis.IGNORED_COLUMNS[table]
        assert set(cols) == known, (table, set(cols) ^ known)


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
