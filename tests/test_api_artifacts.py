"""Tests for Migration Slice 28: artifact convention + download endpoint."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from services import artifact_service
from services.service_errors import InvalidInputError


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def did(isolated_db):
    return isolated_db.create_drama(title_en="D")


def _write(did, kind, name, data=b"hello"):
    path = artifact_service.output_path(did, kind, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_download_and_info(client, did):
    _write(did, "subtitle", "a b.srt", b"12345")
    r = client.get(f"/api/artifacts/dramas/{did}/subtitle")
    assert r.status_code == 200 and r.content == b"12345"
    assert r.headers["content-disposition"] == 'attachment; filename="a_b.srt"'
    info = client.get(f"/api/artifacts/dramas/{did}/subtitle/info").json()
    assert info == {"name": "a b.srt", "size": 5, "kind": "subtitle"}


def test_newest_file_wins(client, did):
    old = _write(did, "epub", "old.epub", b"old")
    os.utime(old, (1, 1))
    _write(did, "epub", "new.epub", b"new")
    assert client.get(f"/api/artifacts/dramas/{did}/epub").content == b"new"


def test_missing_and_unknown(client, did):
    assert client.get(f"/api/artifacts/dramas/{did}/video").status_code == 404
    assert client.get("/api/artifacts/dramas/9999/video").status_code == 404
    assert client.get(f"/api/artifacts/dramas/{did}/secrets").status_code == 422


@pytest.mark.parametrize("bad", ["../x", "a/b", "/etc/passwd", "..", "", "a\\b", "x\x00y"])
def test_output_path_rejects_bad_names(did, bad):
    with pytest.raises(InvalidInputError):
        artifact_service.output_path(did, "subtitle", bad)


def test_traversal_kind_rejected(client, did):
    r = client.get(f"/api/artifacts/dramas/{did}/..%2F..%2Fx")
    assert r.status_code in (404, 422)


def test_symlink_escape_ignored(client, did, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP")
    folder = os.path.dirname(artifact_service.output_path(did, "audio", "keep.txt"))
    os.symlink(secret, os.path.join(folder, "link.txt"))
    r = client.get(f"/api/artifacts/dramas/{did}/audio")
    assert r.status_code == 404
    assert "TOP" not in r.text and str(tmp_path) not in r.text


def test_symlink_dir_escape_ignored(client, did, tmp_path, isolated_db):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "f.txt").write_text("TOP")
    exports = os.path.join(isolated_db.drama_dir(did), "exports")
    os.makedirs(exports)
    os.symlink(tmp_path / "out", os.path.join(exports, "video"))
    r = client.get(f"/api/artifacts/dramas/{did}/video")
    assert r.status_code == 404


def test_errors_and_info_leak_no_path(client, did, isolated_db):
    _write(did, "archive", "z.zip")
    body = client.get(f"/api/artifacts/dramas/{did}/archive/info").text
    assert isolated_db.LIBRARY_DIR not in body
    err = client.get(f"/api/artifacts/dramas/{did}/video").text
    assert isolated_db.LIBRARY_DIR not in err


# --- friendly download names -------------------------------------------------

from services import export_service


def _drama(isolated_db, **fields):
    fields.setdefault("title_en", "Healing spell copies everything")
    return isolated_db.create_drama(**fields)


def _disposition(resp):
    return resp.headers["content-disposition"]


def test_softsub_download_uses_title_episode_and_language(client, isolated_db):
    did = _drama(isolated_db, episode_number=12)
    path = _write(did, "softsub_video", f"softsub_video_{did}.mkv")
    artifact_service.set_download_language(did, "softsub_video", os.path.basename(path), "en")
    r = client.get(f"/api/artifacts/dramas/{did}/softsub_video")
    cd = _disposition(r)
    assert 'filename="Healing spell copies everything - Ep 12 - soft sub (en).mkv"' in cd
    assert "filename*=UTF-8''Healing%20spell%20copies%20everything%20-%20Ep%2012%20-%20soft%20sub%20%28en%29.mkv" in cd
    # The stored name and the info route keep the ID-only name.
    assert artifact_service.get_artifact(did, "softsub_video")["name"] == f"softsub_video_{did}.mkv"
    info = client.get(f"/api/artifacts/dramas/{did}/softsub_video/info").json()
    assert info["name"] == f"softsub_video_{did}.mkv"


@pytest.mark.parametrize("kind,stored,language,expected", [
    ("video", "burned_video_{d}.mp4", "en", "Ep 3 - burned-in (en).mp4"),
    ("dubbed_video", "dubbed_video_{d}.mp4", "en", "Ep 3 - dubbed (en).mp4"),
    ("audio", "audiobook_{d}.m4b", "ja", "Ep 3 - audiobook (ja).m4b"),
    ("scanlate_zip", "typeset_pages.zip", "translated en", "Ep 3 - pages (translated en).zip"),
    ("scanlate_pdf", "typeset_pages.pdf", "translated en", "Ep 3 - pages (translated en).pdf"),
])
def test_each_media_kind_names_its_language(client, isolated_db, kind, stored, language, expected):
    did = _drama(isolated_db, title_en="Show", episode_number=3)
    name = stored.format(d=did)
    _write(did, kind, name)
    artifact_service.set_download_language(did, kind, name, language)
    r = client.get(f"/api/artifacts/dramas/{did}/{kind}")
    assert f'filename="Show - {expected}"' in _disposition(r)


def test_no_language_or_episode_drops_those_parts(client, isolated_db):
    did = _drama(isolated_db, title_en="Show")
    _write(did, "dubbed_video", f"dubbed_video_{did}.mp4")
    r = client.get(f"/api/artifacts/dramas/{did}/dubbed_video")
    assert 'filename="Show - dubbed.mp4"' in _disposition(r)


def test_stale_language_label_is_ignored(isolated_db):
    did = _drama(isolated_db, title_en="Show")
    old = _write(did, "video", "old.mp4")
    artifact_service.set_download_language(did, "video", "old.mp4", "zh")
    new = _write(did, "video", "new.mp4")
    os.utime(old, (1, 1))
    os.utime(new, (2_000_000_000, 2_000_000_000))
    art = artifact_service.get_artifact(did, "video")
    assert art["name"] == "new.mp4" and art["language"] == ""


def test_label_write_refuses_a_planted_symlink(isolated_db, did, tmp_path):
    _write(did, "video", "v.mp4")
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    base = os.path.dirname(artifact_service.output_path(did, "video", "v.mp4"))
    os.symlink(victim, os.path.join(base, ".download.json"))
    artifact_service.set_download_language(did, "video", "v.mp4", "en")
    assert victim.read_text() == "keep"
    assert artifact_service.get_artifact(did, "video")["language"] == ""


def test_label_write_leaves_no_temp_file_and_survives_a_deleted_drama(isolated_db, did):
    _write(did, "video", "v.mp4")
    artifact_service.set_download_language(did, "video", "v.mp4", "en")
    base = os.path.dirname(artifact_service.output_path(did, "video", "v.mp4"))
    assert sorted(os.listdir(base)) == [".download.json", "v.mp4"]
    isolated_db.delete_drama(did)
    artifact_service.set_download_language(did, "video", "v.mp4", "en")  # must not raise


def test_source_title_is_the_fallback_and_cjk_survives(client, isolated_db):
    did = isolated_db.create_drama(title_en="", title_zh="治愈魔法什么都能复制", episode_number=1)
    _write(did, "dubbed_video", f"dubbed_video_{did}.mp4")
    cd = _disposition(client.get(f"/api/artifacts/dramas/{did}/dubbed_video"))
    assert 'filename="__________ - Ep 1 - dubbed.mp4"' in cd
    assert "filename*=UTF-8''%E6%B2%BB%E6%84%88" in cd
    cd.encode("ascii")


@pytest.mark.parametrize("title,expected_stem", [
    ("a/b\\c:d*e?f\"g<h>i|j", "a b c d e f g h i j"),
    ("Name. . .", "Name"),
    ("CON", "CON"),
    ("NUL", "NUL"),
    ("   spaced    out   ", "spaced out"),
    ("emoji 🎬 time", "emoji 🎬 time"),
    ("CON.x", "_CON.x"),
    ("nul.txt", "_nul.txt"),
    ("COM1.foo", "_COM1.foo"),
    ("COM\u00b9.foo", "_COM\u00b9.foo"),
    ("LPT\u00b3 .foo", "_LPT\u00b3 .foo"),
    ("Mr. Smith", "Mr. Smith"),
    ("evil\u202etxt.exe", "eviltxt.exe"),
    ("zero\u200bwidth\u2066x\u2069", "zerowidthx"),
    ("\ufeffBOM title", "BOM title"),
    ("c1\x85ctl", "c1ctl"),
])
def test_download_filename_sanitises(isolated_db, title, expected_stem):
    did = isolated_db.create_drama(title_en=title)
    name = export_service.download_filename(did, "soft sub", "en", ".mkv")
    assert name == f"{expected_stem} - soft sub (en).mkv"
    assert not name.split(".")[0].strip().upper() in {"CON", "NUL", "PRN", "AUX"}


def test_download_filename_only_symbols_falls_back_to_drama_id(isolated_db):
    did = isolated_db.create_drama(title_en="???***")
    assert export_service.download_filename(did, "dubbed", "", "mp4") == f"drama {did} - dubbed.mp4"


def test_download_filename_is_capped_without_cutting_extension_or_pairs(isolated_db):
    did = isolated_db.create_drama(title_en="🎬" * 400, episode_number=5)
    name = export_service.download_filename(did, "bilingual subtitles", "zh+en", "srt")
    assert len(name.encode("utf-8")) <= 255
    assert name.endswith(" - Ep 5 - bilingual subtitles (zh+en).srt")
    name.encode("utf-8")  # a split surrogate pair would raise here
    assert set(name.split(" - ")[0]) == {"🎬"}


def test_download_filename_stem_fits_the_filesystem_limit_for_cjk(isolated_db):
    did = isolated_db.create_drama(title_en="治" * 300, episode_number=2 ** 63 - 1)
    name = export_service.download_filename(did, "bilingual subtitles", "愈" * 80, "srt")
    assert len(name.encode("utf-8")) <= 255
    assert name.endswith(".srt") and name.startswith("治" * 10)
    name.encode("utf-8")


def test_content_disposition_cannot_be_injected():
    cd = export_service.content_disposition('evil"\r\nSet-Cookie: x=1\\.mkv')
    assert "\r" not in cd and "\n" not in cd
    assert cd.count('"') == 2
    assert cd.startswith('attachment; filename="evil_')
    assert "filename*=UTF-8''" in cd
    cd.encode("ascii")


def test_content_disposition_has_ascii_fallback_and_utf8_name():
    cd = export_service.content_disposition("治愈 - Ep 1.srt")
    assert 'filename="__ - Ep 1.srt"' in cd
    assert "filename*=UTF-8''%E6%B2%BB%E6%84%88%20-%20Ep%201.srt" in cd


def test_subtitle_downloads_name_the_field_language(client, isolated_db):
    from core import Line
    did = _drama(isolated_db, source_language="ja", episode_number=12)
    isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="こんにちは", en="Hello")])
    cases = {"en": "subtitles (en)", "zh": "subtitles (ja)", "bilingual": "bilingual subtitles (ja+en)"}
    for field, label in cases.items():
        r = client.get(f"/api/export/dramas/{did}/subtitle", params={"fmt": "srt", "field": field})
        cd = _disposition(r)
        assert f'filename="Healing spell copies everything - Ep 12 - {label}.srt"' in cd
        assert "filename*=UTF-8''" in cd
