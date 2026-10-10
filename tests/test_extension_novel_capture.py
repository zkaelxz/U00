"""The extension's "Save text into a title": page_server's POST /novel and
services/extension_novel_service.py. The answer reaches a script on a
third-party page, so besides the save itself these pin what it may say:
booleans and ids, no text, URL or filesystem path."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import background_jobs
import db
import page_server
from lib.errors import NotFoundError
from services import extension_novel_service as novel
from sources import chapter_manifest, pipeline

from tests.test_page_server import _post

ROOT = Path(__file__).resolve().parents[1]
URL = "https://novel.example/book/12/ch-3.html?token=abc"
CHAPTER = "第三章 夜雨\n\n" + "雨下了一整夜。" * 60


@pytest.fixture
def token(isolated_db):
    return page_server.load_or_create_token()


@pytest.fixture
def title(isolated_db):
    return db.create_drama(title_zh="书", media_type="novel")


def _save(token, drama_id, text=CHAPTER, heading="第三章 夜雨", url=URL, **extra):
    return _post(token, {"drama_id": drama_id, "heading": heading, "text": text,
                         "source": "novel.example", "url": url, **extra}, path="/novel")


def _raw(drama_id):
    with open(chapter_manifest.raw_path(drama_id), encoding="utf-8") as f:
        return f.read()


def test_the_route_sits_behind_the_bridge_token_and_loopback_check(token, title):
    assert _save(None, title).status == 401
    assert _save("wrong", title).status == 401
    assert _post(token, {"drama_id": title, "text": CHAPTER}, path="/novel",
                 client=("192.168.1.9", 5000)).status == 403
    assert not os.path.exists(chapter_manifest.raw_path(title))


def test_saves_a_new_chapter_and_answers_with_booleans_and_ids_only(token, title):
    h = _save(token, title)
    assert h.status == 200, h.payload
    assert h.payload["saved"] is True and h.payload["already_saved"] is False
    assert h.payload["drama_id"] == title
    body = json.dumps(h.payload, ensure_ascii=False)
    assert "雨下了" not in body and "novel.example" not in body and db.LIBRARY_DIR not in body
    chapters = chapter_manifest.load(title)["chapters"]
    assert [c["title"] for c in chapters] == ["第三章 夜雨"]
    assert chapters[0]["source"] == "novel.example"
    assert chapters[0]["url"] == "https://novel.example/book/12/ch-3.html"
    assert _raw(title).startswith("第三章 夜雨\n\n第三章 夜雨\n\n雨下了")


def test_a_repeat_capture_of_the_same_page_adds_nothing(token, title):
    assert _save(token, title).payload["saved"] is True
    again = _save(token, title)
    assert again.status == 200
    assert again.payload["saved"] is False and again.payload["already_saved"] is True
    assert "already saved" in again.payload["message"]
    assert len(chapter_manifest.load(title)["chapters"]) == 1
    assert _raw(title).count("雨下了一整夜") == 60


def test_same_title_different_text_or_same_text_different_title_is_a_new_chapter(token, title):
    _save(token, title)
    assert _save(token, title, text=CHAPTER + "多一句。").payload["saved"] is True
    assert _save(token, title, heading="第四章").payload["saved"] is True
    assert len(chapter_manifest.load(title)["chapters"]) == 3


def test_an_earlier_upload_without_a_manifest_is_never_overwritten(token, title):
    path = chapter_manifest.raw_path(title)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("an uploaded novel")
    _save(token, title)
    assert _raw(title).startswith("an uploaded novel\n\n第三章 夜雨")


def test_control_and_bidi_characters_are_stripped(token, title):
    _save(token, title, text="甲\x00乙\x07丙‮丁\r\n戊 己\t庚", heading="章\x1b[31m一\n二")
    raw = _raw(title)
    assert "甲乙丙丁\n戊\n己\t庚" in raw
    assert re.search(r"[\x00-\x08\x0b-\x1f\x7f‮]", raw) is None
    assert chapter_manifest.load(title)["chapters"][0]["title"] == "章[31m一 二"


def test_text_over_the_cap_is_refused_and_nothing_written(token, title):
    h = _save(token, title, text="字" * (novel.MAX_CAPTURE_CHARS + 1))
    assert h.status == 413
    assert f"{novel.MAX_CAPTURE_CHARS:,}" in h.payload["error"]
    assert not os.path.exists(chapter_manifest.raw_path(title))


def test_empty_after_cleaning_and_bad_ids_are_refused(token, title):
    assert _save(token, title, text="\x00\x01  \n").status == 422
    assert _save(token, "1").status == 422
    assert _save(token, True).status == 422


def test_only_a_novel_title_takes_text(token, isolated_db):
    comic = db.create_drama(title_zh="漫", media_type="comic")
    h = _save(token, comic)
    assert h.status == 422 and "novel" in h.payload["error"]


def test_a_missing_title_is_404(token, title):
    assert _save(token, title + 50).status == 404


def test_ownership_is_checked_for_the_principal(isolated_db):
    private = db.create_drama(title_zh="私", media_type="novel", owner_user_id=999, is_private=1)
    member = {"user_id": 5, "is_admin": False, "is_local_owner": False}
    with pytest.raises(NotFoundError):
        novel.save_page_text(private, "h", CHAPTER, principal=member)
    assert not os.path.exists(chapter_manifest.raw_path(private))
    # The bridge itself acts as the PC's local owner.
    assert novel.save_page_text(private, "h", CHAPTER)["saved"] is True


def test_refused_while_a_chapter_import_runs_for_the_title(token, title, monkeypatch):
    monkeypatch.setattr(background_jobs, "is_running",
                        lambda job_id: job_id == pipeline.import_job_id(title))
    assert _save(token, title).status == 409


def test_the_source_link_is_set_once_and_cleaned(token, title):
    _save(token, title)
    assert db.get_drama(title)["source_url"] == "https://novel.example/book/12/ch-3.html"
    _save(token, title, heading="第四章", url="https://other.example/x")
    assert db.get_drama(title)["source_url"] == "https://novel.example/book/12/ch-3.html"


def test_a_write_failure_names_no_path(token, title, monkeypatch):
    def boom(*a, **k):
        raise PermissionError(13, "denied", os.path.join(db.LIBRARY_DIR, "secret", "raw.txt"))
    monkeypatch.setattr(pipeline, "append_novel_chapter_once", boom)
    h = _save(token, title)
    assert h.status == 500
    assert db.LIBRARY_DIR not in h.payload["error"] and "secret" not in h.payload["error"]


def test_a_service_message_is_redacted_before_it_leaves(token, title, monkeypatch):
    from lib.errors import ConflictError

    def busy(*a, **k):
        raise ConflictError("busy sk-ant-api03-" + "A" * 40)
    monkeypatch.setattr(pipeline, "append_novel_chapter_once", busy)
    h = _save(token, title)
    assert h.status == 409 and "A" * 40 not in h.payload["error"]


def test_the_extension_cap_matches_the_service():
    match = re.search(r"const MAX_NOVEL_CHARS = (\d+);", (ROOT / "extension" / "content.js").read_text("utf-8"))
    assert match and int(match.group(1)) == novel.MAX_CAPTURE_CHARS


# -- the extension side, in Node against a fake page (tests/js/extension_harness.mjs) --

def _node(kind, scenario):
    if shutil.which("node") is None:
        pytest.skip("needs node")
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "extension_harness.mjs"), kind, scenario],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_prose_page_is_a_text_page_and_a_comic_page_is_not():
    out = _node("novel", "classify")
    assert out == {"prose": True, "comic": False, "short": False}


def test_the_selection_wins_over_the_main_text():
    out = _node("novel", "selection_wins")
    assert out["sent"]["text"] == "只要这一段。" and out["fromSelection"] is True
    assert out["sent"]["heading"] == "第三章 夜雨"


def test_the_main_text_block_is_sent_without_a_selection():
    out = _node("novel", "main_text")
    assert out["sent"]["text"].startswith("雨下了一整夜") and out["fromSelection"] is False
    assert out["sent"]["source"] == "novel.example"


def test_an_over_cap_page_is_refused_before_sending():
    out = _node("novel", "over_cap")
    assert out["ok"] is False and out["sentCount"] == 0


def test_the_worker_posts_to_the_bridge_and_drops_a_private_page_url():
    out = _node("novel", "worker")
    assert out["public"]["path"] == "/novel"
    assert out["public"]["body"]["url"] == "https://novel.example/ch/3"
    assert out["public"]["body"]["drama_id"] == 4
    assert out["private"]["body"]["url"] == "" and out["private"]["body"]["source"] == ""


def test_the_popup_button_names_the_title_and_the_result_says_what_happened():
    out = _node("novel", "popup_labels")
    assert out["none"] == "Save text into a title"
    assert out["novel"] == "Save text into 书"
    assert out["comic"] == "Pick a novel title to save text"
    assert "new chapter" in out["saved"] and "selected text" in out["saved"]
    assert "nothing was added" in out["repeat"]
