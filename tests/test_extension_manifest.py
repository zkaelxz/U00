"""
tests/test_extension_manifest.py -- static checks on the browser
extension (Step 34).

The extension is the one part of this project that runs inside a browser
alongside every site the person visits, and it holds a token that can
drive the app. Its safety rests on a handful of properties that are easy
to break by accident later and impossible to notice from the Python side:

  - it may only ever talk to loopback,
  - it must have no standing access to any site,
  - the token must live only in the service worker, never in a page's
    JavaScript context and never in a URL.

None of that is checked by running the app, so it is checked here. These
are the same kind of static guards `tests/test_static_analysis.py` exists
for, applied to JavaScript this test suite can't execute.
"""
import json
import os
import struct

import pytest

import page_server

EXTENSION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension")


def _read(name):
    with open(os.path.join(EXTENSION_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _code(name):
    """`name` with `//` comment lines stripped, so a check for what the
    code *does* isn't tripped by a comment explaining what it must not
    do -- which is exactly what happened the first time this ran."""
    lines = []
    for line in _read(name).splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        lines.append(line.split("//")[0] if "//" in line and "://" not in line else line)
    return "\n".join(lines)


@pytest.fixture(scope="module")
def manifest():
    return json.loads(_read("manifest.json"))


class TestTheManifest:
    def test_it_is_manifest_v3(self, manifest):
        assert manifest["manifest_version"] == 3

    def test_every_file_it_names_exists(self, manifest):
        named = [
            manifest["background"]["service_worker"],
            manifest["action"]["default_popup"],
            manifest["options_page"],
        ]
        for name in named:
            assert os.path.exists(os.path.join(EXTENSION_DIR, name)), name

    def test_its_icons_exist_at_the_declared_sizes(self, manifest):
        """A missing or wrong-sized file makes Chrome fall back to the
        generic letter tile (or refuse to load the extension)."""
        declared = dict(manifest["icons"])
        declared_action = manifest["action"]["default_icon"]
        assert set(declared) == {"16", "32", "48", "128"}
        assert set(declared_action) == {"16", "32"}
        for size, name in {**declared, **declared_action}.items():
            with open(os.path.join(EXTENSION_DIR, name), "rb") as fh:
                header = fh.read(24)
            assert header[:8] == b"\x89PNG\r\n\x1a\n", name
            width, height = struct.unpack(">II", header[16:24])
            assert (width, height) == (int(size), int(size)), name

    def test_it_can_only_reach_the_bridge_port(self, manifest):
        """The bridge's port is fixed, and a loopback pattern without it
        would also cover the API on 8600 and every other local listener."""
        assert manifest["host_permissions"] == [f"http://127.0.0.1:{page_server.DEFAULT_PORT}/*"]

    def test_it_has_no_standing_access_to_any_site(self, manifest):
        """No `content_scripts` block: the content script is injected on a
        click via activeTab, so the extension touches a page only when
        the person asks it to."""
        assert "content_scripts" not in manifest

    def test_other_sites_are_only_ever_optional_and_granted_per_origin(self, manifest):
        """The worker downloads an image the page won't let a script read,
        but only from an origin the person allowed with a click."""
        assert "<all_urls>" not in manifest["optional_host_permissions"]
        assert set(manifest["optional_host_permissions"]) == {"https://*/*", "http://*/*"}
        popup = _code("popup.js")
        assert "chrome.permissions.request({ origins: access.origins })" in popup
        assert "permissions.request" not in _code("background.js")
        assert "permissions.request" not in _code("content.js")

    def test_permissions_stay_minimal(self, manifest):
        assert set(manifest["permissions"]) == {"activeTab", "scripting", "storage"}
        for wide in ("<all_urls>", "tabs", "webRequest", "cookies", "history"):
            assert wide not in manifest["permissions"]
            assert wide not in manifest["host_permissions"]


class TestTheTokenStaysInTheServiceWorker:
    def test_only_the_service_worker_sends_the_token_header(self):
        """The content script shares a page's world closely enough that a
        shared secret there is asking to leak."""
        assert page_server.TOKEN_HEADER in _code("background.js")
        for page_side in ("content.js", "popup.js"):
            assert page_server.TOKEN_HEADER not in _code(page_side), page_side

    def test_the_content_script_never_reads_the_token_from_storage(self):
        content = _code("content.js")
        assert "storage.local" not in content
        assert "token" not in content.lower()

    def test_the_header_name_matches_the_server(self):
        """A rename on one side alone would fail every request with a
        401, so both sides are pinned to the same constant."""
        assert page_server.TOKEN_HEADER == "X-Baihe-Token"
        assert f'"{page_server.TOKEN_HEADER}": token' in _code("background.js")

    def test_the_token_never_goes_into_a_url(self):
        """This repo's standing rule (translate_engines.redact_secrets): a
        URL reaches logs, history and referrers, so nothing token-shaped
        may be built into one."""
        for name in ("background.js", "content.js", "popup.js", "options.js"):
            source = _code(name)
            for pattern in ("token=", "?token", "&token", "/${token}", "+ token"):
                assert pattern not in source, f"{name} puts the token in a URL ({pattern})"


class TestItAgreesWithTheServer:
    def test_the_default_port_matches(self):
        assert f"BRIDGE_PORT = {page_server.DEFAULT_PORT}" in _code("background.js")

    def test_the_port_is_not_a_setting(self):
        """page_server always binds DEFAULT_PORT, so a port field would
        only let someone type a value that can never connect."""
        assert 'id="port"' not in _read("options.html")
        for name in ("background.js", "options.js"):
            assert 'get(["token", "port"' not in _code(name)
            assert "stored.port" not in _code(name)

    def test_an_old_stored_port_is_dropped_on_update(self):
        assert 'storage.local.remove("port")' in _code("background.js")

    def test_it_targets_loopback_by_address_not_by_name(self):
        """`localhost` can resolve to an IPv6 address the server isn't
        listening on, and the server's own peer check is written against
        the literal loopback addresses."""
        background = _code("background.js")
        assert "127.0.0.1" in background
        assert "localhost" not in background

    def test_it_calls_only_the_endpoints_the_server_serves(self):
        background = _code("background.js")
        for route in ("/health", "/page", "/pages", "/text"):
            assert route in background
        # Nothing else: a call to a route the server doesn't serve would
        # be a silent 404 the person sees only as "that didn't work".
        assert "/upload" not in background and "/translate" not in background


class TestBatchSizeMatchesTheServer:
    def test_content_script_batches_at_the_servers_limit(self):
        import re
        match = re.search(r"const MAX_IMAGES_PER_REQUEST = (\d+);", _read("content.js"))
        assert match, "content.js must declare MAX_IMAGES_PER_REQUEST"
        assert int(match.group(1)) == page_server.MAX_IMAGES_PER_REQUEST


class TestChapterCaptureStaysPolite:
    """The scroll-through capture reads what the reader has already
    rendered; these pin the numbers and shapes that keep it that way."""

    def _const(self, name):
        import re
        match = re.search(rf"const {name} = (\d+);", _code("content.js"))
        assert match, f"content.js must declare {name}"
        return int(match.group(1))

    def test_the_page_cap_is_the_documented_300(self):
        assert self._const("CAPTURE_MAX_PAGES") == 300

    def test_steps_are_paced_like_a_person_scrolling(self):
        low, high = self._const("CAPTURE_STEP_MIN_MS"), self._const("CAPTURE_STEP_MAX_MS")
        assert 250 <= low < high <= 600

    def test_the_scroll_step_matches_the_server_side_scroll(self):
        import re
        import page_scroll
        assert "innerHeight * 0.9" in page_scroll.SCROLL_THROUGH_JS
        assert re.search(r"const CAPTURE_STEP_FRACTION = 0\.9;", _code("content.js"))

    def test_capture_sends_through_the_shared_batcher_at_the_servers_limit(self):
        code = _code("content.js")
        assert code.count("async function sendInBatches(") == 1
        run = code[code.index("async function runCapture("):]
        assert "sendInBatches(batch," in run
        assert "limit = MAX_IMAGES_PER_REQUEST" in run
        assert self._const("MAX_IMAGES_PER_REQUEST") == page_server.MAX_IMAGES_PER_REQUEST

    def test_translate_is_refused_while_a_capture_runs(self):
        code = _code("content.js")
        visible = code[code.index("async function translateVisible("):]
        assert "if (state.capture)" in visible[:visible.index("looksLikeChallengePage")]
        popup = _code("popup.js")
        capturing = popup[popup.index("function showCapturing("):popup.index("async function runCapture(")]
        assert "els.translate.disabled = running" in capturing
        assert "els.translateAll.disabled = running" in capturing

    def test_canvas_draw_target_is_its_pixel_hash(self):
        code = _code("content.js")
        assert "canvasIds" not in code
        run = code[code.index("async function runCapture("):]
        assert "srcKey: drawTargetKey(el, extracted.hash)" in run
        assert "await currentDrawTargetKey(el) === srcKey" in run
        assert 'toBlob(' in code[code.index("async function currentDrawTargetKey"):code.index("function showCaptureChip")]

    def test_capture_done_is_sent_on_every_exit_and_popup_saves_overlay(self):
        code = _code("content.js")
        capture = code[code.index("async function captureChapter("):code.index("async function runCapture(")]
        assert capture.index("finally") < capture.index('type: "captureDone"')
        assert code.count('type: "captureDone"') == 1
        popup = _code("popup.js")
        start = popup[popup.index("async function startCapture("):popup.index("async function syncCaptureUi")]
        assert "chrome.storage.local.set({ overlay: els.overlay.checked })" in start
        run = popup[popup.index("async function run(all)"):popup.index("function showCapturing")]
        assert "els.captureChapter.disabled = true" in run
        assert "pageRunInFlight = false" in run

    def test_double_send_guards_claim_before_any_await(self):
        popup = _code("popup.js")
        run = popup[popup.index("async function run(all)"):popup.index("function showCapturing")]
        capture = popup[popup.index("async function runCapture("):popup.index("async function startCapture(")]
        guard = "if (pageRunInFlight || captureInFlight) return;"
        assert guard in run and guard in capture
        assert run.index("pageRunInFlight = true") < run.index("await")
        assert capture.index("captureInFlight = true") < capture.index("await")

    def test_capture_makes_no_calls_of_its_own(self):
        code = _code("content.js")
        capture = code[code.index("const CAPTURE_MAX_PAGES"):code.index("function cancelCapture")]
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "new WebSocket"):
            assert banned not in capture


class TestTextCaptureStaysWithinTheSameModel:
    """Step 96's text-capture mode is a second input surface on the same
    extension, not a second extension -- it has to follow the same rules
    as the existing image mode: no fetch from the content script (which
    shares a page's world), and no per-site code anywhere."""

    def test_the_content_script_never_fetches_directly(self):
        """All network calls go through the service worker -- see
        background.js's own docstring on why. content.js only ever asks
        it via chrome.runtime.sendMessage."""
        assert "fetch(" not in _code("content.js")

    def test_no_hardcoded_site_domains_anywhere_in_the_extension(self):
        """This is a general-purpose extension with no per-site code -- it
        reads whatever page is open the same way for every one of them."""
        known_sites = ("mangaz.com", "manhuaku", "bilibili", "jjwxc",
                      "wuxiaworld", "webnovel", "ranobes")
        for name in ("background.js", "content.js", "popup.js", "options.js"):
            source = _code(name).lower()
            for site in known_sites:
                assert site not in source, f"{name} references {site}"


class TestImageHashWorksOnPlainHttpPages:
    def test_a_page_without_crypto_subtle_still_gets_a_key(self):
        """crypto.subtle only exists on secure contexts; a plain-http reader
        would otherwise fail every capture."""
        code = _code("content.js")
        assert "crypto.subtle" in code and "weakHash(buffer)" in code
        assert code.index("weakHash(buffer)") < code.index("crypto.subtle.digest")


class TestThePopupSaysWherePagesWent:
    def test_the_open_link_targets_the_comic_route_with_only_an_id(self):
        import api.api_config as api_config
        popup = _code("popup.js")
        assert f'APP_URL = "http://127.0.0.1:{api_config.DEFAULT_PORT}"' in popup
        assert "/#/comic/${dramaId}" in popup
        assert "token" not in popup.lower().split("showopenlink", 1)[1].split("}", 1)[0]

    def test_opening_the_link_needs_no_new_permission(self, manifest):
        assert "tabs" not in manifest["permissions"]
        assert manifest["host_permissions"] == ["http://127.0.0.1:8756/*"]

    def test_the_result_line_names_the_destination_and_the_unsaved_case(self):
        popup = _code("popup.js")
        for wording in ("Sent ${sent} page", "already translated, not sent again",
                        "on the page only, not saved"):
            assert wording in popup

    def test_the_markup_keeps_its_ids_and_shows_the_full_drama_title(self):
        html = _read("popup.html")
        for element_id in ("drama", "store", "status", "openInBaihe", "dramaTitle"):
            assert f'id="{element_id}"' in html
        assert 'aria-live="polite"' in html
        assert "width: 340px" in html
