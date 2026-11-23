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
    job = {"drama_id": None, "source_language": "", "typeset": True, "typeset_style": None,
           "store": False, "member": True}
    assert bridge._disk_cached(bridge._quick_read_url_key(
        {"fetch_url": "https://cdn.example.com/1.png"}, job)) is not None


def test_a_saved_send_is_never_cached(pipeline, monkeypatch):
    job = {"drama_id": 1, "source_language": "", "typeset": False, "typeset_style": None, "store": True,
           "member": False}
    assert bridge._quick_read_url_key({"fetch_url": "https://x.test/1.png"}, job) is None


def test_cache_drops_old_and_oversized_entries(isolated_db, monkeypatch):
    quick_read_cache.put("old", {"a": 1})
    path = quick_read_cache._path("old")
    os.utime(path, (time.time() - 30 * 24 * 3600,) * 2)
    assert quick_read_cache.get("old") is None
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
