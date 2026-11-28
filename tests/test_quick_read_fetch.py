"""Quick read by picture address: the PC downloads, falls back to the phone, and caches results."""

import base64
import io
import os
import time

import pytest

from lib import http
from lib.errors import InvalidInputError
from services import extension_bridge_service as bridge
from services import page_server, quick_read_cache, quick_read_fetch


def _png_bytes(shade=240):
    Image = pytest.importorskip("PIL.Image")
    img = Image.new("RGB", (600, 900), (shade, shade, shade))
    img.paste((10, 10, 10), (150, 225, 450, 450))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def pipeline(isolated_db, monkeypatch):
    calls = []

    def fake_translate(data, content_type, **kw):
        calls.append(len(data))
        return {"regions": [], "notes": [], "typeset_image": "data:image/jpeg;base64,AAAA"}

    monkeypatch.setattr(page_server, "translate_image", fake_translate)
    return calls


def _run(images, **extra):
    return bridge.run_images({"store": False, "typeset": True, "filter_pages": False, **extra}, images)


def _serve(monkeypatch, table):
    seen = []

    def fake_get(url, **kw):
        seen.append((url, kw["headers"]))
        outcome = table[url]
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return http.Response(status, {}, body, url)

    monkeypatch.setattr(http, "get", fake_get)
    return seen


def test_pc_downloads_the_picture_and_sends_no_upload(pipeline, monkeypatch):
    seen = _serve(monkeypatch, {"https://cdn.example.com/1.png": (200, _png_bytes())})
    answer = _run([{"key": "a", "fetch_url": "https://cdn.example.com/1.png"}],
                  fetch_referer="https://example.com/ch1", fetch_user_agent="UA/1")
    assert [p["key"] for p in answer["pages"]] == ["a"]
    assert "unfetched" not in answer
    assert seen[0][1]["Referer"] == "https://example.com/ch1"
    assert seen[0][1]["User-Agent"] == "UA/1"


@pytest.mark.parametrize("outcome,reason", [
    ((403, b"no"), "blocked"), ((404, b""), "missing"), ((500, b""), "unreachable"),
    (http.FetchError(), "unreachable"), (http.ResponseTooLarge(), "too_large"),
    (InvalidInputError("private"), "unsafe_address"), ((200, b"<html>"), "not_image"),
])
def test_a_picture_the_pc_cannot_get_is_named_for_the_phone_to_upload(pipeline, monkeypatch, outcome, reason):
    _serve(monkeypatch, {"https://cdn.example.com/1.png": outcome})
    answer = _run([{"key": "a", "fetch_url": "https://cdn.example.com/1.png"}])
    assert answer["pages"] == [] and answer["failed"] == []
    assert answer["unfetched"] == [{"key": "a", "url": "https://cdn.example.com/1.png", "reason": reason}]
    assert pipeline == []


def test_mixed_batch_keeps_uploads_and_downloads(pipeline, monkeypatch):
    _serve(monkeypatch, {"https://cdn.example.com/1.png": (200, _png_bytes()),
                         "https://cdn.example.com/2.png": (403, b"")})
    uploaded = base64.b64encode(_png_bytes(200)).decode()
    answer = _run([{"key": "a", "fetch_url": "https://cdn.example.com/1.png"},
                   {"key": "b", "fetch_url": "https://cdn.example.com/2.png"},
                   {"key": "c", "data": uploaded, "content_type": "image/png"}])
    assert [p["key"] for p in answer["pages"]] == ["a", "c"]
    assert [u["key"] for u in answer["unfetched"]] == ["b"]


def test_non_http_address_is_never_requested(pipeline, monkeypatch):
    seen = _serve(monkeypatch, {})
    answer = _run([{"key": "a", "fetch_url": "file:///etc/passwd"}])
    assert answer["unfetched"][0]["reason"] == "unsafe_address" and seen == []


def test_cookie_goes_only_to_the_chapters_own_site():
    options = {"referer": "https://example.com/ch1", "cookie": "s=1"}
    assert quick_read_fetch.request_headers("https://cdn.example.com/1.png", options)["Cookie"] == "s=1"
    assert "Cookie" not in quick_read_fetch.request_headers("https://evil.test/1.png", options)
    assert "Cookie" not in quick_read_fetch.request_headers("https://example.com/1.png", {"cookie": "s=1"})


def test_reopening_a_chapter_skips_the_download_and_the_model(pipeline, monkeypatch):
    seen = _serve(monkeypatch, {"https://cdn.example.com/1.png": (200, _png_bytes())})
    image = {"key": "a", "fetch_url": "https://cdn.example.com/1.png"}
    _run([image])
    bridge._quick_read_cache.clear()   # as after a restart: only the disk cache remains
    second = _run([{**image, "key": "z"}])
    assert len(seen) == 1 and len(pipeline) == 1
    assert second["pages"][0]["cached"] is True and second["pages"][0]["key"] == "z"


def test_another_member_gets_the_cached_page(pipeline, monkeypatch):
    _serve(monkeypatch, {"https://cdn.example.com/1.png": (200, _png_bytes())})
    _run([{"key": "a", "fetch_url": "https://cdn.example.com/1.png"}])
    bridge._quick_read_cache.clear()
    job = {"drama_id": None, "source_language": "", "typeset": True, "typeset_style": None, "typeset_max_width": None, "glossary_version": bridge._glossary_version(None),
           "store": False, "member": True}
    assert bridge._disk_cached(bridge._quick_read_url_key(
        {"fetch_url": "https://cdn.example.com/1.png"}, job)) is not None


def test_a_saved_send_is_never_cached(pipeline, monkeypatch):
    job = {"drama_id": 1, "source_language": "", "typeset": False, "typeset_style": None, "typeset_max_width": None, "glossary_version": "", "store": True,
           "member": False}
    assert bridge._quick_read_url_key({"fetch_url": "https://x.test/1.png"}, job) is None


def test_cache_drops_old_and_oversized_entries(isolated_db, monkeypatch):
    quick_read_cache.put("old", {"a": 1})
    path = quick_read_cache._path("old")
    now = time.time()
    monkeypatch.setattr(quick_read_cache.time, "time", lambda: now + 30 * 24 * 3600)
    assert quick_read_cache.get("old") is None
    monkeypatch.undo()
    monkeypatch.setattr(quick_read_cache, "MAX_BYTES", 300)
    for i in range(5):
        quick_read_cache.put(f"k{i}", {"pad": "x" * 100})
        time.sleep(0.02)
    left = [n for n in os.listdir(quick_read_cache._dir()) if n.endswith(".json")]
    assert len(left) <= 2 and "k4.json" in left


def test_a_saved_send_does_not_fetch(pipeline, monkeypatch):
    seen = _serve(monkeypatch, {})
    with pytest.raises(page_server.EndpointError) as caught:
        bridge.run_images({"store": True, "drama_id": None},
                          [{"key": "a", "fetch_url": "https://cdn.example.com/1.png"}])
    assert caught.value.status == 400 and seen == []


@pytest.mark.parametrize("address", ["http://[::1/x", "https://host.example:99999/x"])
def test_a_malformed_address_is_unfetched_not_a_500(pipeline, monkeypatch, address):
    answer = _run([{"key": "a", "fetch_url": address}])
    assert answer["unfetched"][0]["reason"] == "unsafe_address"


def test_a_cookie_fetch_is_not_remembered_by_address(pipeline, monkeypatch):
    _serve(monkeypatch, {"https://cdn.example.com/1.png": (200, _png_bytes())})
    image = {"key": "a", "fetch_url": "https://cdn.example.com/1.png"}
    _run([image], fetch_referer="https://example.com/ch", fetch_cookie="s=1")
    bridge._quick_read_cache.clear()
    # Only the bytes entry exists: no address alias was written for the cookie fetch.
    assert len(os.listdir(quick_read_cache._dir())) == 1
    seen_after = _run([image])   # no cookie: downloaded again; it is the same page only because its bytes match
    assert len(pipeline) == 1 and seen_after["pages"][0]["cached"] is True


def test_cookie_needs_https_and_the_chapters_own_host():
    options = {"referer": "https://example.com/ch1", "cookie": "s=1"}
    assert "Cookie" not in quick_read_fetch.request_headers("http://example.com/1.png", options)
    assert "Cookie" not in quick_read_fetch.request_headers("https://example.com.evil.test/1.png", options)
    parent = {"referer": "https://read.example.com/ch1", "cookie": "s=1"}
    assert "Cookie" not in quick_read_fetch.request_headers("https://example.com/1.png", parent)


def test_one_request_cannot_download_more_than_the_total(monkeypatch):
    monkeypatch.setattr(quick_read_fetch, "MAX_TOTAL_BYTES", 1500)
    monkeypatch.setattr(quick_read_fetch, "FETCHES_AT_ONCE", 1)
    monkeypatch.setattr(quick_read_fetch, "fetch_one", lambda url, options, max_bytes: (b"x" * 1000, ""))
    got = quick_read_fetch.fetch_all({"a": "u1", "b": "u2", "c": "u3"}, {}, 12 * 1024 * 1024)
    assert [got[k][1] for k in "abc"] == ["", "", "too_large"]


def test_a_member_has_two_download_slots(monkeypatch):
    sem = quick_read_fetch._slots("m1")
    assert sem.acquire(blocking=False) and sem.acquire(blocking=False)
    assert not sem.acquire(blocking=False)
    assert quick_read_fetch._slots("m2").acquire(blocking=False)


def test_edited_glossary_changes_the_cache_key(isolated_db, monkeypatch):
    drama = isolated_db.create_drama(title_zh="t", title_en="t", media_type="manga")
    before = bridge._glossary_version(drama)
    monkeypatch.setattr(isolated_db, "list_run_glossary_terms",
                        lambda d: [{"term_original": "阿洛", "term_translation": "RLuo"}])
    assert bridge._glossary_version(drama) != before
    assert bridge._glossary_version(None) != ""


def test_comic_glossary_edit_changes_the_cache_key(isolated_db):
    from services import settings_service
    before = bridge._glossary_version(None)
    settings_service.set_settings({"comic_glossary_custom": "ドン = thud"})
    assert bridge._glossary_version(None) != before


def test_a_change_to_the_pipeline_code_changes_the_cache_key(monkeypatch):
    job = {"drama_id": None, "source_language": "", "typeset": True, "typeset_style": None,
           "typeset_max_width": None, "glossary_version": "", "store": False, "member": False}
    image = {"content": b"page"}
    before = bridge._quick_read_key(image, job)
    monkeypatch.setattr(quick_read_cache, "pipeline_version", lambda: "after-an-upgrade")
    assert bridge._quick_read_key(image, job) != before


def test_the_pipeline_version_follows_the_code_files(tmp_path, monkeypatch):
    quick_read_cache.pipeline_version.cache_clear()
    first = quick_read_cache.pipeline_version()
    assert first == quick_read_cache.pipeline_version() and len(first) == 16
    monkeypatch.setattr(quick_read_cache, "_PIPELINE_FILES", ("ocr.py",))
    quick_read_cache.pipeline_version.cache_clear()
    assert quick_read_cache.pipeline_version() != first
    quick_read_cache.pipeline_version.cache_clear()


def test_health_says_the_app_reads_picture_addresses(isolated_db):
    assert bridge.health_payload()["picture_addresses"] is True


def _b64_image(shade):
    return {"key": f"p{shade}", "data": base64.b64encode(_png_bytes(shade)).decode()}


def test_a_picture_repeated_across_requests_of_one_run_is_read_once(pipeline):
    run = {"run_id": "run-repeat-0001"}
    first = _run([_b64_image(240)], **run)
    # The same picture listed again by the page lands in a later request of the same read.
    second = _run([{**_b64_image(240), "key": "again"}, _b64_image(200)], **run)
    assert [p["key"] for p in first["pages"]] == ["p240"]
    assert [p["key"] for p in second["pages"]] == ["p200"]
    assert second["skipped"][0]["key"] == "again"
    assert len(pipeline) == 2


def test_the_same_picture_in_another_run_is_still_read(pipeline):
    _run([_b64_image(240)], run_id="run-repeat-0002")
    answer = _run([_b64_image(240)], run_id="run-repeat-0003")
    assert [p["key"] for p in answer["pages"]] == ["p240"]


def test_a_picture_that_failed_may_be_sent_again_in_the_same_run(pipeline, monkeypatch):
    run = {"run_id": "run-repeat-0004"}
    real = page_server.translate_image
    monkeypatch.setattr(page_server, "translate_image",
                        lambda *a, **k: (_ for _ in ()).throw(page_server.EndpointError(500, "boom")))
    assert _run([_b64_image(240)], **run)["failed"]
    monkeypatch.setattr(page_server, "translate_image", real)
    assert [p["key"] for p in _run([_b64_image(240)], **run)["pages"]] == ["p240"]


def test_a_page_asked_again_in_the_same_run_is_answered_not_skipped(pipeline):
    run = {"run_id": "run-repeat-0005"}
    first = _run([_b64_image(240), _b64_image(200)], **run)
    assert [p["key"] for p in first["pages"]] == ["p240", "p200"]
    # The phone lost the connection before p200 arrived and asks for it again.
    again = _run([_b64_image(200)], **run)
    assert [p["key"] for p in again["pages"]] == ["p200"] and not again["skipped"]
    # A copy of it under another key is still a repeat.
    copy = _run([{**_b64_image(200), "key": "copy"}], **run)
    assert copy["pages"] == [] and copy["skipped"][0]["key"] == "copy"


def _job(**over):
    job = {"drama_id": None, "source_language": "", "typeset": True, "typeset_style": None,
           "typeset_max_width": None, "glossary_version": "", "store": False, "member": False}
    return {**job, **over}


def _config(**over):
    return {"engine": "e", "model": "m", "ocr_backend": "", "detect_backend": "auto",
            "prefer_paddle_vl_manga": False, **over}


@pytest.mark.parametrize("change", [{"ocr_backend": "paddle"}, {"detect_backend": "yolo"},
                                    {"prefer_paddle_vl_manga": True}])
def test_reader_settings_change_the_cache_key(isolated_db, change):
    assert bridge._key_settings(_job(), _config()) != bridge._key_settings(_job(), _config(**change))


def test_text_colour_matching_changes_the_cache_key(isolated_db):
    from services import settings_service
    before = bridge._key_settings(_job(), _config())
    settings_service.set_settings({"scanlate_match_text_color": not settings_service.get("scanlate_match_text_color")})
    assert bridge._key_settings(_job(), _config()) != before


def test_a_kept_series_fix_changes_the_cache_key(isolated_db):
    series = isolated_db.create_series("Fix Series")
    drama = isolated_db.create_drama(title_zh="t", title_en="t", media_type="manga", series_id=series)
    before = bridge._glossary_version(drama)
    isolated_db.record_translation_memory(series, "你好", "Hi")
    added = bridge._glossary_version(drama)
    assert added != before
    isolated_db.record_translation_memory(series, "你好", "Hello there")
    assert bridge._glossary_version(drama) != added


def test_an_edit_to_the_series_cast_changes_the_cache_key(isolated_db):
    series = isolated_db.create_series("Cast Series")
    drama = isolated_db.create_drama(title_zh="t", title_en="t", media_type="manga", series_id=series)
    before = bridge._glossary_version(drama)
    isolated_db.upsert_series_character(series, "Alex Example")
    assert bridge._glossary_version(drama) != before


def test_sending_the_page_picture_changes_the_cache_key(isolated_db):
    from services import settings_service
    before = bridge._key_settings(_job(), _config())
    settings_service.set_settings({"scanlate_page_image": not settings_service.get("scanlate_page_image")})
    assert bridge._key_settings(_job(), _config()) != before


def test_a_change_to_the_prompt_code_changes_the_pipeline_version():
    assert "translation_guide.py" in quick_read_cache._PIPELINE_FILES
    assert "engine_backends" in quick_read_cache._PIPELINE_DIRS


def test_an_entry_that_keeps_being_read_still_expires(isolated_db, monkeypatch):
    start = time.time()
    clock = [start]
    monkeypatch.setattr(quick_read_cache.time, "time", lambda: clock[0])
    real_utime = os.utime
    # A hit touches the file; on the faked clock that touch is "now" too.
    monkeypatch.setattr(os, "utime", lambda path, times=None: real_utime(path, times or (clock[0], clock[0])))
    quick_read_cache.put("busy", {"a": 1})
    for day in range(1, 14):
        clock[0] = start + day * 24 * 3600
        assert quick_read_cache.get("busy") == {"a": 1}
    clock[0] = start + 15 * 24 * 3600
    assert quick_read_cache.get("busy") is None


def test_a_titles_memory_outlives_a_page_but_not_180_days(isolated_db, monkeypatch):
    start = time.time()
    clock = [start]
    monkeypatch.setattr(quick_read_cache.time, "time", lambda: clock[0])
    real_utime = os.utime
    monkeypatch.setattr(os, "utime", lambda path, times=None: real_utime(path, times or (clock[0], clock[0])))
    quick_read_cache.put("page", {"a": 1})
    quick_read_cache.remember_title("series", {"names": {"A": "Alex"}})
    clock[0] = start + 100 * 24 * 3600
    assert quick_read_cache.get("page") is None
    assert quick_read_cache.title_memory("series") == {"names": {"A": "Alex"}}
    clock[0] = start + 181 * 24 * 3600
    assert quick_read_cache.title_memory("series") == {}


def test_a_blank_picture_is_not_stored(pipeline, monkeypatch):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, "PNG")
    _serve(monkeypatch, {"https://cdn.example.com/blank.png": (200, buf.getvalue())})
    _run([{"key": "a", "fetch_url": "https://cdn.example.com/blank.png"}])
    assert not [n for n in os.listdir(quick_read_cache._dir()) if n.endswith(".json")]
