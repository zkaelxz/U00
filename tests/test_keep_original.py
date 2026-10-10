"""The per-title "Keep original files" pin (dramas.keep_original): written
through the metadata update, kept by every storage preset, flagged in the
Trash, and carried by the library export. Temp folders only."""
import os
import time
import zipfile

import pytest

import background_jobs
import db
import storage
from core import Line
from services import disk_usage_service as dus
from services import disk_usage_trash_service as dts
from services import drama_service, keep_original_service
from services import library_admin_service as las
from services.service_errors import InvalidInputError

ORIGINALS = ["source.mp4", "audio.wav", "novel_reference.txt", "raw_novel_context.txt",
             "raw_transcript.json", "raw_transcript.20260101T000000Z.json", "pages/p_0001.png"]


def _write(path, data=b"x" * 100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _title(pinned, name="T"):
    did = db.create_drama(title_en=name, media_type="video_drama", status="translated")
    db.update_drama(did, audio_filename="audio.wav", source_video_filename="source.mp4",
                    novel_reference_filename="novel_reference.txt", keep_original=int(pinned))
    base = os.path.join(db.DRAMAS_DIR, str(did))
    for rel in ORIGINALS:
        _write(os.path.join(base, rel))
    _write(os.path.join(base, "dub_clips", "l1.wav"))
    db.create_page(did, 0, "pages/p_0001.png", 10, 10)
    return did, base


@pytest.fixture
def greedy_category(monkeypatch):
    """A cleanup category whose name patterns match every original, so the
    test proves the pin (not today's harmless patterns) keeps them."""
    cats = dict(storage.CLEANABLE_CATEGORIES)
    cats["greedy"] = {"label": "Greedy", "note": "", "regenerable": True,
                      "pattern_suffixes": [".mp4", ".wav", ".txt", ".json"]}
    cats["greedy_pages"] = {"label": "Greedy pages", "note": "", "regenerable": True,
                            "pattern_subdir": "pages", "pattern_suffixes": [".png"]}
    monkeypatch.setattr(storage, "CLEANABLE_CATEGORIES", cats)
    real = storage.categories_for_preset
    monkeypatch.setattr(storage, "categories_for_preset",
                        lambda key: real(key) + ["greedy", "greedy_pages"])


def _exists(base):
    return {rel for rel in ORIGINALS if os.path.exists(os.path.join(base, rel))}


# ----- writing the pin -------------------------------------------------------

def test_pin_is_written_through_the_metadata_whitelist(isolated_db):
    did = db.create_drama(title_en="T")
    assert drama_service.update_drama_metadata(did, keep_original=True)["keep_original"] == 1
    assert db.get_drama(did)["keep_original"] == 1
    drama_service.update_drama_metadata(did, keep_original=False)
    assert db.get_drama(did)["keep_original"] == 0
    with pytest.raises(InvalidInputError):
        drama_service.update_drama_metadata(did, keep_original="yes")


def test_pin_route_needs_the_metadata_permission(isolated_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from api import auth as api_auth
    from api.api_config import ApiSettings
    from api.server import create_app
    from services import auth_service
    did = db.create_drama(title_en="T")
    c = TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False)),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)

    def headers(*perms, email, admin=False):
        u = auth_service.grant_admin_local(email) if admin else auth_service.add_user(email)
        for p in perms:
            auth_service.grant_permission(u["id"], p)
        s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
        return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                api_auth.CSRF_HEADER: s["csrf_token"]}
    url = f"/api/dramas/{did}/metadata"
    reader = headers("library.read", email="r@example.com")
    assert c.post(url, json={"keep_original": True}, headers=reader).status_code == 403
    assert db.get_drama(did)["keep_original"] == 0
    editor = headers(email="e@example.com", admin=True)    # admin.library comes with admin
    r = c.post(url, json={"keep_original": True}, headers=editor)
    assert r.status_code == 200 and r.json()["keep_original"] is True
    detail = c.get(f"/api/library/dramas/{did}", headers=editor).json()
    assert detail["keep_original"] is True


# ----- storage presets -------------------------------------------------------

@pytest.mark.parametrize("preset", list(storage.STORAGE_QUALITY_PRESETS))
def test_no_preset_removes_a_pinned_titles_originals(isolated_db, greedy_category, preset):
    pinned, pbase = _title(True, "Pinned")
    loose, lbase = _title(False, "Loose")
    scan = las.storage_scan(preset)
    by_id = {d["drama_id"]: d for d in scan["per_drama"]}
    assert by_id[pinned]["would_free_bytes"] < by_id[loose]["would_free_bytes"]
    las.storage_cleanup(preset, confirm=True, confirm_text=las.CLEAN_CONFIRM_TEXT)
    assert _exists(pbase) == set(ORIGINALS)
    assert _exists(lbase) == set()            # the same patterns do reach an unpinned title
    if "dub_clips" in storage.categories_for_preset(preset):
        assert not os.path.exists(os.path.join(pbase, "dub_clips"))   # not an original


def test_originals_list_ignores_names_outside_the_folder_and_links(isolated_db, tmp_path):
    did, base = _title(True)
    outside = tmp_path / "elsewhere.wav"
    outside.write_bytes(b"x")
    db.update_drama(did, audio_filename="../../elsewhere.wav")
    os.symlink(outside, os.path.join(base, "raw_novel_context.link"))
    db.update_drama(did, source_video_filename="raw_novel_context.link")
    rels = {rel for rel, _full in keep_original_service.original_files(did)}
    assert "../../elsewhere.wav" not in rels and "raw_novel_context.link" not in rels
    assert "raw_transcript.json" in rels and os.path.join("pages", "p_0001.png") in rels


# ----- Trash -----------------------------------------------------------------

def test_trash_list_flags_items_from_a_pinned_title(isolated_db):
    pinned, pbase = _title(True, "Pinned")
    loose, lbase = _title(False, "Loose")
    lib = os.path.basename(db.LIBRARY_DIR)
    for did in (pinned, loose):
        dus.clear(f"{lib}/dramas/{did}/dub_clips", confirm=True, expected_size_bytes=100,
                  expected_file_count=1)
    flags = {i["original_path_relative"].split("/")[2]: i["from_kept_title"]
             for i in dts.trash_list()["items"]}
    assert flags == {str(pinned): True, str(loose): False}


def test_trash_flag_needs_a_title_folder():
    assert not keep_original_service.trashed_from_pinned_title(["library", "backups", "1"])
    assert not keep_original_service.trashed_from_pinned_title(["model_cache"])
    assert not keep_original_service.trashed_from_pinned_title([])


# ----- export ----------------------------------------------------------------

def _export(*ids):
    for did in ids:
        db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    job = las.start_export_zip(list(ids))["job_id"]
    end = time.time() + 10
    while (background_jobs.get_status(job) or {}).get("status") not in ("done", "error"):
        assert time.time() < end, "export did not finish"
        time.sleep(0.05)
    assert background_jobs.get_status(job)["status"] == "done"
    with zipfile.ZipFile(las.admin_artifact_path("export")["path"]) as zf:
        return zf.namelist()


def test_export_carries_a_pinned_titles_originals_only(isolated_db):
    pinned, _ = _title(True, "Pinned")
    loose, _ = _title(False, "Loose")
    names = _export(pinned, loose)
    kept = {n.split("/originals/", 1)[1] for n in names
            if n.startswith(f"{pinned} ") and "/originals/" in n}
    assert kept == set(ORIGINALS)
    assert not any(n.startswith(f"{loose} ") and "/originals/" in n for n in names)
    assert any(n.startswith(f"{loose} ") and n.endswith("english.srt") for n in names)
