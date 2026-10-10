"""
A reader that draws a cross-origin image without CORS taints the canvas the
extension reads from, so toBlob() throws. The extension then has the service
worker download the file instead. These run content.js and background.js
against a fake browser (tests/js/extension_harness.mjs): no network, no real
site, no real browser.
"""
import json
import os
import shutil
import subprocess

import pytest

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js", "extension_harness.mjs")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(kind, scenario):
    done = subprocess.run([NODE, HARNESS, kind, scenario], capture_output=True, text=True,
                          timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


class TestPageSideFallback:
    def test_a_tainted_img_is_downloaded_by_the_worker_and_sent(self):
        out = _run("content", "img_tainted_fetches_in_worker")
        assert out["ok"] is True
        assert out["fetched"] == ["https://s1.bzcdn.net/p/1.jpg"]
        assert out["sent"] == [{"content_type": "image/jpeg", "url": "https://s1.bzcdn.net/p/1.jpg", "bytes": 12}]

    def test_a_tainted_canvas_uses_the_sibling_img_url(self):
        out = _run("content", "canvas_tainted_url_from_sibling_img")
        assert out["ok"] is True
        assert out["fetched"] == ["https://s1.bzcdn.net/p/1.jpg"]
        assert out["sent"][0]["content_type"] == "image/png"

    def test_a_tainted_canvas_uses_a_data_attribute_url(self):
        out = _run("content", "canvas_tainted_url_from_data_attribute")
        assert out["ok"] is True and len(out["sent"]) == 1

    def test_two_canvases_each_use_the_image_at_their_own_position(self):
        out = _run("content", "two_canvases_map_to_their_own_images")
        assert out["ok"] is True
        assert out["fetched"] == ["https://s1.bzcdn.net/p/1.jpg", "https://s1.bzcdn.net/p/2.jpg"]
        assert len(out["sent"]) == 2 and out["unverified"] == 2

    def test_ambiguous_sibling_images_are_refused_not_guessed(self):
        out = _run("content", "ambiguous_images_map_to_no_canvas")
        assert out["ok"] is False and out["fetched"] == [] and out["sent"] == []
        assert "wouldn't let this page's image be read" in out["error"]

    def test_a_sibling_image_that_is_elsewhere_on_screen_is_not_used(self):
        out = _run("content", "an_image_elsewhere_is_not_the_canvas_image")
        assert out["ok"] is False and out["fetched"] == []

    def test_a_partial_read_still_offers_the_missing_site(self):
        out = _run("content", "partial_read_still_asks_for_the_site")
        assert out["ok"] is True and len(out["sent"]) == 1
        assert out["partialOrigins"] == ["https://s1.bzcdn.net"]

    def test_no_discoverable_url_keeps_the_original_clear_error(self):
        out = _run("content", "canvas_tainted_no_url")
        assert out["ok"] is False and "code" not in out
        assert out["fetched"] == [] and out["sent"] == []
        assert "wouldn't let this page's image be read" in out["error"]
        assert "Tainted canvases may not be exported" in out["error"]

    def test_a_file_that_is_not_the_drawn_page_is_not_used(self):
        out = _run("content", "canvas_tainted_file_is_not_the_drawn_page")
        assert out["ok"] is False and out["sent"] == []
        assert "doesn't match what the page draws" in out["error"]

    def test_a_worker_refusal_is_reported_as_the_reason(self):
        out = _run("content", "img_tainted_worker_rejects")
        assert out["ok"] is False and out["sent"] == []
        assert "isn't a PNG, JPEG or WebP image" in out["error"]

    def test_a_missing_site_permission_asks_for_the_button(self):
        out = _run("content", "img_tainted_needs_permission")
        assert out["ok"] is False and out["sent"] == []
        assert out["code"] == "NEEDS_SITE_ACCESS"
        assert out["origins"] == ["https://s1.bzcdn.net"]
        assert '"Allow" button' in out["error"]


class TestWholeChapterCapture:
    def test_capture_downloads_a_tainted_page_the_same_way(self):
        out = _run("content", "capture_tainted_img_fetches_in_worker")
        assert out["ok"] is True
        assert out["fetched"] == ["https://s1.bzcdn.net/p/1.jpg"]
        assert len(out["sent"]) == 1

    def test_capture_asks_for_the_site_instead_of_scrolling_for_nothing(self):
        out = _run("content", "capture_tainted_img_needs_permission")
        assert out["code"] == "NEEDS_SITE_ACCESS" and out["origins"] == ["https://s1.bzcdn.net"]

    def test_a_tainted_canvas_is_downloaded_once_across_capture_steps(self):
        out = _run("content", "capture_fetches_a_tainted_canvas_once")
        assert out["ok"] is True
        assert out["fetched"] == ["https://s1.bzcdn.net/p/1.jpg"]
        assert len(out["sent"]) == 1 and out["unverified"] == 1

    def test_capture_with_no_url_keeps_the_clear_error(self):
        out = _run("content", "capture_tainted_canvas_no_url")
        assert out["ok"] is False and "Tainted canvases" in out["error"]


class TestWorkerDownload:
    def test_a_valid_image_is_fetched_without_cookies_or_redirects_and_with_a_timeout_signal(self):
        out = _run("background", "valid_jpeg")
        assert out == {"ok": True, "content_type": "image/jpeg", "credentials": "omit",
                       "redirect": "error", "hasSignal": True}

    def test_the_referrer_is_only_the_origin_of_the_page(self):
        out = _run("background", "referrer_is_only_the_page_origin")
        assert out == {"referrer": "https://reader.example/", "referrerPolicy": "origin"}

    def test_no_referrer_is_sent_when_the_page_is_unknown(self):
        assert _run("background", "no_referrer_without_a_page")["keys"] == ["credentials", "redirect", "signal"]

    def test_permission_patterns_come_only_from_a_validated_host(self):
        out = _run("background", "permission_patterns")
        refused = ["https://*/x.jpg", "https://*.victim.com/x.jpg", "https://8.8.8.8/x.jpg"]
        for target in refused:
            assert out[target].get("code") is None, target
            assert out[target]["asked"] == [] and out[target]["fetchCalls"] == 0, target
        expected = {
            "https://a%2eb/x.jpg": "https://a.b/*",
            "https://u:p@host.example/x.jpg": "https://host.example/*",
            "https://HOST.Example/x.jpg": "https://host.example/*",
            "https://bücher.example/x.jpg": "https://xn--bcher-kva.example/*",
            "https://xn--bcher-kva.example/x.jpg": "https://xn--bcher-kva.example/*",
            "https://cdn.example:8443/x.jpg": "https://cdn.example:8443/*",
            "http://cdn.example:80/x.jpg": "http://cdn.example/*",
            "https://cdn.example./x.jpg": "https://cdn.example/*",
        }
        for target, pattern in expected.items():
            assert out[target]["asked"] == [pattern], target
            assert out[target]["code"] == "NEEDS_PERMISSION" and out[target]["fetchCalls"] == 0, target

    def test_the_type_is_decided_by_the_bytes_not_the_header(self):
        assert _run("background", "type_comes_from_the_bytes_not_the_header")["content_type"] == "image/png"

    @pytest.mark.parametrize("scenario", ["html_labelled_as_image", "svg_rejected"])
    def test_a_non_image_response_is_rejected(self, scenario):
        out = _run("background", scenario)
        assert out["ok"] is False and "isn't a PNG, JPEG or WebP" in out["error"]

    @pytest.mark.parametrize("scenario", ["oversize_declared", "oversize_streamed"])
    def test_an_oversize_response_is_rejected(self, scenario):
        out = _run("background", scenario)
        assert out["ok"] is False and "larger than the 12 MB" in out["error"]

    def test_a_slow_server_times_out(self):
        out = _run("background", "timeout")
        assert out["ok"] is False and "took too long" in out["error"]

    def test_an_http_error_is_reported(self):
        assert "HTTP 403" in _run("background", "http_error")["error"]

    def test_nothing_is_fetched_without_the_per_site_permission(self):
        out = _run("background", "needs_permission")
        assert out["code"] == "NEEDS_PERMISSION" and out["origin"] == "https://s1.bzcdn.net"
        assert out["fetchCalls"] == 0

    def test_local_and_private_addresses_and_other_schemes_are_never_fetched(self):
        out = _run("background", "private_hosts_refused")
        assert out and all(v == {"ok": False, "fetchCalls": 0} for v in out.values())


class TestPrivateNamesAreNeverGranted:
    def test_single_label_and_lan_suffixed_hosts_are_never_fetched(self):
        out = _run("background", "private_hosts_refused")
        for target in ("http://nas/x.jpg", "http://camera/snapshot.jpg", "http://router.lan/x.jpg",
                       "http://printer.internal/x.jpg", "http://x.home.arpa/x.jpg", "http://a.intranet/x.jpg",
                       "http://a.corp/x.jpg", "http://a.localdomain/x.jpg", "http://nas./x.jpg"):
            assert out[target] == {"ok": False, "fetchCalls": 0}, target

    def test_the_popup_refuses_the_same_origins_with_the_same_check(self):
        out = _run("popup", "site_patterns")
        assert out["https://s1.bzcdn.net"] == "https://s1.bzcdn.net/*"
        assert out["https://cdn.example:8443"] == "https://cdn.example:8443/*"
        refused = [k for k in out if k not in ("https://s1.bzcdn.net", "https://cdn.example:8443")]
        assert refused and all(out[k] is None for k in refused), out

    def test_the_popup_asks_for_one_origin_per_click(self):
        source = os.path.join(os.path.dirname(HARNESS), "..", "..", "extension", "popup.js")
        with open(source, encoding="utf-8") as fh:
            assert "origins: [pattern]" in fh.read()


class TestTaintedCanvasOverlayKey:
    def test_the_key_is_per_canvas_and_size_and_empty_urls_have_none(self):
        out = _run("popup", "tainted_keys")
        assert out["firstIsStable"] is True
        assert out["twoCanvasesDiffer"] is True
        assert out["resizedDiffers"] is True
        assert out["emptyUrl"] is None
