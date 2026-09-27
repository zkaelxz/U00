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

    def test_it_can_only_reach_loopback(self, manifest):
        """A host permission wider than loopback would let this extension
        talk to anything on the internet with the person's cookies."""
        assert manifest["host_permissions"] == ["http://127.0.0.1/*"]

    def test_it_has_no_standing_access_to_any_site(self, manifest):
        """No `content_scripts` block: the content script is injected on a
        click via activeTab, so the extension touches a page only when
        the person asks it to."""
        assert "content_scripts" not in manifest

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
        assert f"DEFAULT_PORT = {page_server.DEFAULT_PORT}" in _code("background.js")
        assert f'value="{page_server.DEFAULT_PORT}"' in _read("options.html")

    def test_it_targets_loopback_by_address_not_by_name(self):
        """`localhost` can resolve to an IPv6 address the server isn't
        listening on, and the server's own peer check is written against
        the literal loopback addresses."""
        background = _code("background.js")
        assert "127.0.0.1" in background
        assert "localhost" not in background

    def test_it_calls_only_the_endpoints_the_server_serves(self):
        background = _code("background.js")
        for route in ("/health", "/page", "/pages"):
            assert route in background
        # Nothing else: a call to a route the server doesn't serve would
        # be a silent 404 the person sees only as "that didn't work".
        assert "/upload" not in background and "/translate" not in background
