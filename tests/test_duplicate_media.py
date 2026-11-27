"""The same file added twice is refused, and the duplicate finder lists titles that look doubled."""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")


import db
from jobs import job_runner
from services import media_fingerprint_service
from tests.test_api_media_upload import _up, client  # noqa: F401  (the upload fixture)


def test_same_file_into_another_title_is_refused(client, isolated_db):
    first = db.create_drama(title_en="Show A")
    second = db.create_drama(title_en="Show B")
    assert _up(client, first, data=b"same bytes").status_code == 200
    r = _up(client, second, data=b"same bytes")
    assert r.status_code == 409
    assert "Show A" in r.text
    assert db.get_drama(second)["audio_filename"] in (None, "")
    assert os.listdir(db.drama_dir(second)) == []  # the refused copy is not kept


def test_a_different_file_is_fine(client, isolated_db):
    first = db.create_drama(title_en="Show A")
    second = db.create_drama(title_en="Show B")
    assert _up(client, first, data=b"one").status_code == 200
    assert _up(client, second, data=b"two").status_code == 200


def test_a_hidden_title_is_not_a_duplicate_for_the_caller(isolated_db):
    db.create_drama(title_en="Secret", media_hash="h1", owner_user_id=1, is_private=1)
    member = {"user_id": 2, "is_admin": False, "is_local_owner": False}
    media_fingerprint_service.refuse_duplicate("h1", 99, member)  # no answer that confirms the hidden title


def test_scan_does_not_overwrite_media_installed_meanwhile(isolated_db):
    did = db.create_drama(title_en="X", audio_filename="new.mp3", media_hash="fresh")
    assert not db.set_media_hash_if_unchanged(did, "stale", "old.mp3", None)
    assert db.get_drama(did)["media_hash"] == "fresh"


def test_removing_media_or_the_video_drops_the_hash(isolated_db):
    did = db.create_drama(title_en="X", audio_filename="a.mp3", source_video_filename="v.mp4", media_hash="h")
    db.clear_source_video(did, "v.mp4")
    assert db.get_drama(did)["media_hash"] is None and db.get_drama(did)["audio_filename"] == "a.mp3"


def test_groups_by_file_link_and_name(isolated_db):
    a = db.create_drama(title_en="Same Name", media_hash="h", source_url="https://x.test/a")
    b = db.create_drama(title_en="same  name", media_hash="h", source_url="https://x.test/a")
    db.create_drama(title_en="Other", audio_filename="old.mp3")
    result = media_fingerprint_service.duplicate_groups(None)
    kinds = {g["same"]: sorted(i["id"] for i in g["items"]) for g in result["groups"]}
    assert kinds == {"file": [a, b], "link": [a, b], "name": [a, b]}
    assert result["unhashed"] == 1


def test_scan_hashes_titles_without_a_hash(isolated_db):
    did = db.create_drama(title_en="Old", audio_filename="a.mp3")
    with open(os.path.join(db.drama_dir(did), "a.mp3"), "wb") as fh:
        fh.write(b"abc")
    media_fingerprint_service.start_scan()
    job_runner.wait_for_job_threads(5)
    assert db.get_drama(did)["media_hash"] == media_fingerprint_service.hash_file(
        os.path.join(db.drama_dir(did), "a.mp3"))
    assert media_fingerprint_service.duplicate_groups(None)["unhashed"] == 0


def test_links_whose_video_id_is_cut_do_not_match_on_the_bare_host(isolated_db):
    db.create_drama(title_en="One", source_url="https://video.example/watch/BV1aB2cD3eF4")
    db.create_drama(title_en="Two", source_url="https://video.example/watch/BV9xY8zW7vU6")
    assert media_fingerprint_service.duplicate_groups(None)["groups"] == []
