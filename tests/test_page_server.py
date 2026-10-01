"""
tests/test_page_server.py -- Step 34's localhost endpoint.

Mocked throughout, per this repo's testing rules: no real browser, no
real OCR backend, no model download, no network. `scanlate`'s pipeline
functions are monkeypatched, so what's under test here is the endpoint's
own job -- who it answers, what it refuses, and whether it hands back
boxes in the coordinates it promised -- not the translation pipeline,
which has its own tests.

The security assertions are the point of this file. Each one stands for a
rule from the module docstring: local callers only, a token required in a
header, no CORS preflight answered (which is what stops any open tab
driving the app), and bounded input.
"""
import base64
import io
import json
import os
import threading

import pytest

import page_server


def _png_bytes(width=600, height=900, colour=(240, 240, 240)):
    Image = pytest.importorskip("PIL.Image", reason="Pillow builds the fixture image")
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def _b64(data):
    return base64.b64encode(data).decode("ascii")


class _FakeHeaders(dict):
    """`BaseHTTPRequestHandler.headers` is a mapping with a `.get`, which
    is all the handler ever uses."""


class _FakeHandler(page_server._Handler):
    """Drives the real handler's routing and checks without a socket.

    Only the transport is faked: `_check_access`, `_read_body`, the route
    table and `_run` are all the real implementations, so a test that
    passes here is exercising the shipped logic.
    """

    def __init__(self, path="/health", method="GET", headers=None, body=b"",
                 client=("127.0.0.1", 51000)):
        self.path = path
        self.command = method
        self.headers = _FakeHeaders(headers or {})
        self.client_address = client
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.sent = {"status": None, "headers": [], "body": None}

    # -- transport stubs ----------------------------------------------
    def send_response(self, status, message=None):
        self.sent["status"] = status

    def send_header(self, key, value):
        self.sent["headers"].append((key, value))

    def end_headers(self):
        pass

    def log_message(self, fmt, *args):
        pass

    # -- what the test reads ------------------------------------------
    @property
    def status(self):
        return self.sent["status"]

    @property
    def payload(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))

    def header(self, name):
        for key, value in self.sent["headers"]:
            if key.lower() == name.lower():
                return value
        return None


def _post(token, payload, path="/page", client=("127.0.0.1", 51000)):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Length": str(len(body))}
    if token is not None:
        headers[page_server.TOKEN_HEADER] = token
    handler = _FakeHandler(path, "POST", headers, body, client)
    handler.do_POST()
    return handler


def _get(token, path="/health", client=("127.0.0.1", 51000)):
    headers = {}
    if token is not None:
        headers[page_server.TOKEN_HEADER] = token
    handler = _FakeHandler(path, "GET", headers, b"", client)
    handler.do_GET()
    return handler


@pytest.fixture
def token(isolated_db):
    """A real token, created in the isolated library dir so no test ever
    reads or writes the real one."""
    return page_server.load_or_create_token()


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Stands in for scanlate: one bubble with known coordinates, so the
    test can assert the endpoint passes boxes through unchanged."""
    calls = {"detect": [], "translate": [], "saved": []}

    def detect_and_ocr_page(image_path, source_language, **kwargs):
        calls["detect"].append({"path": image_path, "language": source_language,
                                "kwargs": kwargs})
        return ([{"x": 11, "y": 22, "w": 33, "h": 44, "source_text": "原文",
                  "translated_text": "", "kind": "bubble", "font_category": "regular",
                  "reading_order": 0}], [])

    def translate_page_bubbles(bubbles, engine, drama_meta, **kwargs):
        calls["translate"].append({"engine": engine, "kwargs": kwargs})
        for b in bubbles:
            b["translated_text"] = "the translation"
        return "context after this page"

    import scanlate
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect_and_ocr_page)
    monkeypatch.setattr(scanlate, "translate_page_bubbles", translate_page_bubbles)
    return calls


@pytest.fixture(autouse=True)
def _reset_module_state():
    page_server.set_translation_config(engine=None, api_key="", base_url=None,
                                       free_tier=False)
    with page_server._context_lock:
        page_server._contexts.clear()
    yield


class TestTheEndpointRefusesWhatItShould:
    """Each of these is a security rule, not a nicety -- any page in any
    tab can reach localhost."""

    def test_a_request_with_no_token_is_refused(self, token, fake_pipeline):
        handler = _post(None, {"images": [{"data": _b64(_png_bytes()),
                                           "content_type": "image/png"}]})
        assert handler.status == 401
        assert "token" in handler.payload["error"]

    def test_a_request_with_the_wrong_token_is_refused(self, token, fake_pipeline):
        handler = _post(token + "x", {"images": [{"data": _b64(_png_bytes()),
                                                  "content_type": "image/png"}]})
        assert handler.status == 401

    def test_a_non_local_caller_is_refused_even_with_a_valid_token(self, token):
        handler = _get(token, client=("192.168.1.50", 51000))
        assert handler.status == 403
        assert "this computer" in handler.payload["error"]

    def test_health_also_requires_the_token(self, token):
        assert _get(None).status == 401
        assert _get(token).status == 200

    def test_no_cors_preflight_is_answered(self, token):
        """Without a preflight, no web page can get the browser's
        permission to send the token header cross-origin. Answering one
        -- or sending Access-Control-Allow-Origin -- would hand every
        open tab an endpoint that drives this app."""
        handler = _FakeHandler("/page", "OPTIONS",
                               {page_server.TOKEN_HEADER: token})
        handler.do_OPTIONS()
        assert handler.status == 405
        assert handler.header("Access-Control-Allow-Origin") is None

    def test_a_successful_response_carries_no_permissive_cors_header(self, token):
        handler = _get(token)
        assert handler.status == 200
        assert handler.header("Access-Control-Allow-Origin") is None

    def test_an_oversized_body_is_refused_without_being_read(self, token):
        headers = {page_server.TOKEN_HEADER: token,
                   "Content-Length": str(page_server.MAX_BODY_BYTES + 1)}
        handler = _FakeHandler("/page", "POST", headers, b"")
        handler.do_POST()
        assert handler.status == 413

    def test_a_non_image_payload_is_refused(self, token, fake_pipeline, isolated_db):
        handler = _post(token, {"images": [{"data": _b64(b"this is not an image"),
                                            "content_type": "text/html"}]})
        assert handler.status == 415

    def test_too_many_images_are_refused(self, token, fake_pipeline):
        one = {"data": _b64(_png_bytes()), "content_type": "image/png"}
        handler = _post(token, {"images": [one] * (page_server.MAX_IMAGES_PER_REQUEST + 1)},
                        path="/pages")
        assert handler.status == 413

    def test_page_takes_exactly_one_image(self, token, fake_pipeline):
        one = {"data": _b64(_png_bytes()), "content_type": "image/png"}
        assert _post(token, {"images": [one, one]}, path="/page").status == 400

    def test_malformed_json_is_refused_plainly(self, token):
        body = b"{not json"
        handler = _FakeHandler("/page", "POST",
                               {page_server.TOKEN_HEADER: token,
                                "Content-Length": str(len(body))}, body)
        handler.do_POST()
        assert handler.status == 400
        assert "JSON" in handler.payload["error"]

    def test_bad_base64_is_refused_plainly(self, token):
        handler = _post(token, {"images": [{"data": "!!!not base64!!!",
                                            "content_type": "image/png"}]})
        assert handler.status == 400

    def test_an_unknown_endpoint_is_a_404(self, token):
        assert _get(token, path="/anything-else").status == 404
        assert _post(token, {"images": []}, path="/other").status == 404

    def test_a_refusal_closes_the_connection_instead_of_desyncing_it(self, token):
        """Keep-alive is on, and a POST is refused on its headers before
        its body is read. Leaving that body in the socket would make the
        next request on the same connection parse from the middle of it,
        so a refusal must close rather than keep the connection."""
        body = json.dumps({"images": [{"data": "", "content_type": "image/png"}]}).encode()
        handler = _FakeHandler("/page", "POST", {"Content-Length": str(len(body))}, body)
        handler.do_POST()
        assert handler.status == 401
        assert handler.close_connection is True

    def test_the_handler_has_a_per_connection_timeout(self):
        """A peer that opens a socket and stops talking must not hold a
        worker thread for ever."""
        assert page_server._Handler.timeout == page_server.REQUEST_TIMEOUT_SECONDS
        assert page_server._Handler.timeout > 0


class TestTheTokenItself:
    def test_the_token_is_reused_across_calls_not_regenerated(self, isolated_db):
        first = page_server.load_or_create_token()
        assert page_server.load_or_create_token() == first
        assert len(first) >= 32

    def test_the_token_lives_in_the_library_dir_not_the_database(self, isolated_db):
        page_server.load_or_create_token()
        import db
        assert os.path.dirname(page_server.token_path()) == db.LIBRARY_DIR
        assert os.path.exists(page_server.token_path())

    def test_comparison_rejects_a_non_ascii_header_without_raising(self, isolated_db):
        real = page_server.load_or_create_token()
        assert page_server._token_matches("tökén", real) is False
        assert page_server._token_matches("", real) is False
        assert page_server._token_matches(real, real) is True


class TestTranslatingAPage:
    def test_boxes_come_back_in_the_images_own_pixel_coordinates(self, token, fake_pipeline,
                                                                 isolated_db):
        """The overlay maps these by the element's own scale, so they
        must be the pixels of the image that was sent -- unnormalised."""
        data = _png_bytes(700, 1100)
        handler = _post(token, {"images": [{"data": _b64(data), "content_type": "image/png",
                                            "key": "img-1"}], "store": False})
        assert handler.status == 200
        page = handler.payload["pages"][0]
        assert (page["width"], page["height"]) == (700, 1100)
        assert page["regions"] == [{
            "x": 11, "y": 22, "w": 33, "h": 44,
            "source_text": "原文", "translated_text": "",
            "kind": "bubble", "font_category": "regular", "reading_order": 0}]
        assert page["key"] == "img-1"

    def test_with_no_engine_configured_the_source_text_still_comes_back(self, token,
                                                                       fake_pipeline,
                                                                       isolated_db):
        """And it says so, rather than presenting untranslated text as a
        translation."""
        handler = _post(token, {"images": [{"data": _b64(_png_bytes()),
                                            "content_type": "image/png"}], "store": False})
        page = handler.payload["pages"][0]
        assert page["regions"][0]["source_text"] == "原文"
        assert page["regions"][0]["translated_text"] == ""
        assert any("no translation engine" in note[1] for note in page["notes"])
        assert fake_pipeline["translate"] == []

    def test_a_configured_engine_is_used_and_the_translation_returned(self, token,
                                                                     fake_pipeline,
                                                                     isolated_db, monkeypatch):
        import translate_engines
        monkeypatch.setattr(translate_engines, "get_engine",
                            lambda *a, **kw: object())
        page_server.set_translation_config(engine="claude", api_key="test-key")
        handler = _post(token, {"images": [{"data": _b64(_png_bytes()),
                                            "content_type": "image/png"}], "store": False})
        page = handler.payload["pages"][0]
        assert page["regions"][0]["translated_text"] == "the translation"
        assert len(fake_pipeline["translate"]) == 1

    def test_a_failing_engine_still_returns_the_ocr_text(self, token, fake_pipeline,
                                                         isolated_db, monkeypatch):
        """A translation failure must not throw away real OCR text -- the
        same choice the Scanlate tab makes."""
        import scanlate
        import translate_engines
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **kw: object())

        def boom(*a, **kw):
            raise ValueError("the engine returned nothing")
        monkeypatch.setattr(scanlate, "translate_page_bubbles", boom)
        page_server.set_translation_config(engine="claude", api_key="test-key")
        handler = _post(token, {"images": [{"data": _b64(_png_bytes()),
                                            "content_type": "image/png"}], "store": False})
        assert handler.status == 200
        page = handler.payload["pages"][0]
        assert page["regions"][0]["source_text"] == "原文"
        assert any("translation failed" in note[1] for note in page["notes"])

    def test_an_unstored_page_leaves_no_temp_file_behind(self, token, fake_pipeline,
                                                        isolated_db):
        seen = {}

        import scanlate
        real = scanlate.detect_and_ocr_page

        def capture(image_path, *a, **kw):
            seen["path"] = image_path
            return real(image_path, *a, **kw)
        scanlate.detect_and_ocr_page = capture
        try:
            _post(token, {"images": [{"data": _b64(_png_bytes()),
                                      "content_type": "image/png"}], "store": False})
        finally:
            scanlate.detect_and_ocr_page = real
        assert seen["path"] and not os.path.exists(seen["path"])

    def test_a_page_can_be_stored_into_a_drama_with_its_bubbles(self, token, fake_pipeline,
                                                                isolated_db):
        import db
        drama_id = db.create_drama(title_en="Test Manga", media_type="manga",
                                   source_language="ja")
        handler = _post(token, {"images": [{"data": _b64(_png_bytes()),
                                            "content_type": "image/png"}],
                                "drama_id": drama_id, "store": True})
        assert handler.status == 200
        page = handler.payload["pages"][0]
        assert page["stored"] is True and page["page_id"]
        # It went in through the real import path, so the library has the
        # page row and its bubbles.
        assert len(db.list_pages(drama_id)) == 1
        assert db.load_bubbles(page["page_id"])[0]["source_text"] == "原文"

    def test_the_dramas_own_source_language_is_used(self, token, fake_pipeline, isolated_db):
        import db
        drama_id = db.create_drama(title_en="JP", media_type="manga", source_language="ja")
        _post(token, {"images": [{"data": _b64(_png_bytes()), "content_type": "image/png"}],
                      "drama_id": drama_id, "store": False})
        assert fake_pipeline["detect"][-1]["language"] == "ja"

    def test_an_unknown_drama_is_refused(self, token, fake_pipeline, isolated_db):
        handler = _post(token, {"images": [{"data": _b64(_png_bytes()),
                                            "content_type": "image/png"}],
                                "drama_id": 999999})
        assert handler.status == 404

    def test_health_lists_the_dramas_the_extension_can_send_to(self, token, isolated_db):
        import db
        drama_id = db.create_drama(title_en="Pick me", media_type="manhua")
        payload = _get(token).payload
        assert payload["ok"] is True
        assert any(d["id"] == drama_id and d["title"] == "Pick me"
                   for d in payload["dramas"])


class TestSeveralImagesAtOnce:
    def test_obvious_non_pages_are_dropped_by_the_existing_filter(self, token, fake_pipeline,
                                                                 isolated_db):
        """Reuses `sources/generic_import.py`'s filter rather than a
        second implementation in JavaScript that would drift from it."""
        pages = [{"data": _b64(_png_bytes(800, 1200, (240, 240 - i * 20, 240))),
                  "content_type": "image/png",
                  "key": f"p{i}", "url": f"https://site.invalid/p{i}.png"} for i in range(3)]
        icon = {"data": _b64(_png_bytes(48, 48)), "content_type": "image/png",
                "key": "icon", "url": "https://site.invalid/icon.png"}
        handler = _post(token, {"images": pages + [icon], "store": False,
                                "source_url": "https://site.invalid/chapter/1"},
                        path="/pages")
        assert handler.status == 200
        assert [p["key"] for p in handler.payload["pages"]] == ["p0", "p1", "p2"]
        assert [s["key"] for s in handler.payload["skipped"]] == ["icon"]

    def test_a_single_deliberate_send_is_never_filtered_away(self, token, fake_pipeline,
                                                            isolated_db):
        """A person pointing at one image means that image, even if it is
        small enough that the page filter would have dropped it."""
        handler = _post(token, {"images": [{"data": _b64(_png_bytes(120, 120)),
                                            "content_type": "image/png"}], "store": False})
        assert handler.status == 200
        assert len(handler.payload["pages"]) == 1

    def test_a_set_with_nothing_page_shaped_is_refused_clearly(self, token, fake_pipeline,
                                                              isolated_db):
        icons = [{"data": _b64(_png_bytes(40, 40 + i, (10 * i, 20, 30))),
                  "content_type": "image/png",
                  "key": f"i{i}", "url": f"https://site.invalid/i{i}.png"} for i in range(3)]
        handler = _post(token, {"images": icons, "store": False,
                                "source_url": "https://site.invalid/x"}, path="/pages")
        assert handler.status == 422


class TestTranslatingCapturedText:
    """Step 96's text-capture mode: `/text` runs the same security checks
    as `/page`/`/pages` (covered generically above, since `_check_access`
    is shared code) plus its own input rules, then funnels into
    `translate_engines.standalone_translate` -- the same function
    `tabs/translate_tab.py` already uses -- rather than a second
    translation path.
    """

    def _post_text(self, token, **payload):
        body = {"text": "some captured page text"}
        body.update(payload)
        return _post(token, body, path="/text")

    def test_text_is_required(self, token):
        assert self._post_text(token, text="").status == 400
        assert self._post_text(token, text="   ").status == 400

    def test_oversized_text_is_refused(self, token):
        handler = self._post_text(token, text="x" * (page_server.MAX_TEXT_CHARS + 1))
        assert handler.status == 413

    def test_an_unknown_language_is_refused(self, token):
        assert self._post_text(token, source_language="fr").status == 400

    def test_source_and_target_must_differ(self, token):
        assert self._post_text(token, source_language="en", target_language="en").status == 400

    def test_one_side_must_be_english(self, token):
        """standalone_translate only ever translates one side of a pair
        with English -- see its own docstring -- so a zh->ja request must
        be refused rather than silently mistranslated."""
        handler = self._post_text(token, source_language="zh", target_language="ja")
        assert handler.status == 400

    def test_with_no_engine_configured_the_source_text_still_comes_back(self, token, isolated_db):
        handler = self._post_text(token, text="原文内容")
        assert handler.status == 200
        payload = handler.payload
        assert payload["source_text"] == "原文内容"
        assert payload["translated_text"] == ""
        assert any("no translation engine" in note[1] for note in payload["notes"])
        assert payload["saved_to_history"] is False

    def test_a_configured_engine_translates_and_saves_to_history(self, token, isolated_db,
                                                                 monkeypatch):
        import translate_engines

        class _FakeEngine:
            name = "claude"

            def translate_batch(self, chunks, context):
                return [f"[TEST] {c}" for c in chunks]

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **kw: _FakeEngine())
        page_server.set_translation_config(engine="claude", api_key="test-key")
        handler = self._post_text(token, text="原文内容", source_language="zh",
                                  target_language="en")
        assert handler.status == 200
        payload = handler.payload
        assert payload["translated_text"] == "[TEST] 原文内容"
        assert payload["saved_to_history"] is True

        import db
        history = db.list_translate_history()
        assert history and history[0]["source_text"] == "原文内容"
        assert history[0]["translated_text"] == "[TEST] 原文内容"

    def test_store_false_skips_history(self, token, isolated_db, monkeypatch):
        import translate_engines

        class _FakeEngine:
            name = "claude"

            def translate_batch(self, chunks, context):
                return [f"[TEST] {c}" for c in chunks]

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **kw: _FakeEngine())
        page_server.set_translation_config(engine="claude", api_key="test-key")
        handler = self._post_text(token, text="原文内容", store=False)
        assert handler.payload["saved_to_history"] is False

        import db
        assert db.list_translate_history() == []

    def test_a_failing_engine_still_returns_the_captured_text(self, token, isolated_db,
                                                              monkeypatch):
        """A translation failure must not throw away the captured text --
        the same choice translate_image makes on this failure."""
        import translate_engines

        class _BoomEngine:
            name = "claude"

            def translate_batch(self, chunks, context):
                raise ValueError("the engine returned nothing")

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **kw: _BoomEngine())
        page_server.set_translation_config(engine="claude", api_key="test-key")
        handler = self._post_text(token, text="原文内容")
        assert handler.status == 200
        payload = handler.payload
        assert payload["source_text"] == "原文内容"
        assert payload["translated_text"] == ""
        assert any("translation failed" in note[1] for note in payload["notes"])
        assert payload["saved_to_history"] is False

    def test_an_unsupported_direction_is_refused_with_a_clear_message(self, token, isolated_db,
                                                                      monkeypatch):
        import translate_engines
        monkeypatch.setattr(translate_engines, "standalone_direction_support",
                            lambda *a: (False, "Not supported."))

        class _FakeEngine:
            name = "nllb"

            def translate_batch(self, chunks, context):
                return list(chunks)

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **kw: _FakeEngine())
        page_server.set_translation_config(engine="nllb", api_key="local")
        handler = self._post_text(token, text="hello there", source_language="en",
                                  target_language="zh")
        assert handler.status == 422


class TestStartingTheServer:
    def test_it_starts_once_per_process(self, monkeypatch):
        started = []
        monkeypatch.setattr(page_server, "_server_started", False)
        monkeypatch.setattr(page_server, "_serve", lambda port: started.append(port))
        assert page_server.ensure_server_started(port=18756) is True
        assert page_server.ensure_server_started(port=18756) is False
        assert started == [18756]

    def test_it_binds_loopback_only(self, monkeypatch):
        """The bind address is the first line of defence; the per-request
        peer check is the second."""
        bound = {}

        class FakeServer:
            def __init__(self, address, handler):
                bound["address"] = address
            def serve_forever(self):
                pass

        monkeypatch.setattr(page_server, "ThreadingHTTPServer", FakeServer)
        page_server._serve(18757)
        assert bound["address"][0] == "127.0.0.1"

    def test_a_port_conflict_is_reported_not_claimed_as_running(self, monkeypatch):
        monkeypatch.setattr(page_server, "_server_started", False)

        def boom(port):
            raise OSError("address already in use")
        monkeypatch.setattr(page_server, "_serve", boom)
        page_server.ensure_server_started(port=18758)
        for _ in range(100):
            if not page_server.server_running():
                break
            threading.Event().wait(0.01)
        assert page_server.server_running() is False


class TestTheConfigBridge:
    def test_settings_push_reaches_the_server_thread(self):
        page_server.set_translation_config(engine="deepseek", api_key="k",
                                           tesseract_cmd="/usr/bin/tesseract")
        config = page_server.get_translation_config()
        assert config["engine"] == "deepseek"
        assert config["tesseract_cmd"] == "/usr/bin/tesseract"

    def test_an_unknown_setting_is_ignored_rather_than_raising(self):
        page_server.set_translation_config(not_a_real_setting=1)
        assert "not_a_real_setting" not in page_server.get_translation_config()

    def test_no_engine_without_a_key(self):
        page_server.set_translation_config(engine="claude", api_key="")
        assert page_server._build_engine(page_server.get_translation_config()) is None

    def test_an_engine_name_this_build_does_not_know_reads_as_none(self, isolated_db):
        """/health answers "is an engine configured?" the same way, so a
        stale or misspelled name must not fail the health check."""
        page_server.set_translation_config(engine="not_a_real_engine", api_key="k")
        assert page_server._build_engine(page_server.get_translation_config()) is None
        assert _get(page_server.load_or_create_token()).status == 200
