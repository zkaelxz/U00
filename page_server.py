"""
page_server.py -- the small, localhost-only HTTP endpoint the browser
extension talks to.

**Why this exists at all.** The adapters in `sources/` do bulk import:
they fetch a chapter, track new ones, and build an offline library. This
is the other half -- "translate the page I am looking at right now" --
and it reaches content the adapters structurally can't, *without this app
ever touching a protection mechanism*. On a site whose pages are
tile-scrambled, delivered as `blob:` objects that only exist
inside the rendering tab (manhuaku), or gated behind a signed-in session
(Bilibili Manga), the person's own browser has **already** done the
decrypting, descrambling and authenticating, because they are reading the
page legitimately. An extension reads what is on their screen. No
headless driving, no pacing games, no session to forge. It is strictly
less invasive than what this app already does, and it also covers the
sites with no adapter at all, which is most of them.

**What this module is not.** It is not a second translation pipeline.
Everything here funnels into the existing, tested `scanlate.py` chain
(`detect_and_ocr_page` -> `translate_page_bubbles`) and the existing
`sources.pipeline.add_page_images` import path, which is the same one
Scanlate's own manual upload uses. This module is an *input surface*, and
the deliberate whole of its own logic is: authenticate the caller, bound
what it may send, put the bytes somewhere `scanlate` can read, and hand
back boxes in image coordinates.

Deliberately stdlib-only (`http.server`): no new dependency, so there is
nothing to register in `diagnostics.py`'s `OPTIONAL_DEPENDENCIES`. The
planning session's own note that a browser extension "only needs *some*
small local HTTP endpoint, not necessarily the full FastAPI+React
migration" is correct, and this is that endpoint -- the deferred M8+
migration is not a prerequisite.

## Security, and why each rule is here

- **Bound to `127.0.0.1` only, never `0.0.0.0`.** A local port that
  accepts images and runs OCR must not be reachable from the network.
  The peer address is checked a second time per request, so a future
  change to the bind address can't silently open it up.
- **A token is required on every request, in a header, never in a URL.**
  Any page in any tab can make requests to localhost, so an
  unauthenticated endpoint would let any site quietly drive this app.
  The token follows this repo's existing secret rule
  (`translate_engines.redact_secrets`): header only, and never written
  to a URL, a log line, or a stored error. Compared with
  `secrets.compare_digest`.
- **A custom header is itself the cross-site defence.** A web page
  cannot set one on a cross-origin request without a successful CORS
  preflight, so this server answers no preflight and sends no
  `Access-Control-Allow-Origin` at all. That is why the extension does
  its fetch from its *service worker* (which has `host_permissions` and
  so is not subject to page CORS) rather than from the content script.
  Do not "fix" a CORS error here by adding a permissive header -- that
  would hand every website in the browser an open endpoint.
- **Bounded input.** Body size, per-image size and image count are all
  capped, and only real image content types are accepted, so a stray or
  hostile request can't pin the OCR backend or exhaust memory.
- **Timeouts.** Every connection gets one, per this project's standing
  rule that a hung peer must never leave work stuck forever.

## Threading

`scanlate`'s model caches (`_bubble_ml_model`, `ocr.py`'s
`_paddle_instances`, ...) are plain module globals with no locking, so
two concurrent requests could race on first load. Every pipeline run
therefore happens under `_PIPELINE_LOCK`: requests queue instead of
racing. Only detection and OCR (and storing their result) are serialised;
translation runs outside the lock so an LLM call never stalls other pages
or a Cancel. This is a single-user local app, so serialising is the right
trade rather than a bug.

A stored page is visible to other requests before it is translated; see
`page_turns` for how captures of one page or one title take turns.

## Where the translation settings come from

The server runs on its own background thread with no request session of
the API's to read settings from. `services/extension_service.py` registers
a config provider (`set_config_provider`) that resolves the saved engine
and its key from `.env` server-side on every request, so a key saved in
Settings applies to the next page without a restart; API keys are never
stored in the database.
With no engine configured the endpoint still detects and OCRs, and says
so in its `notes` -- it never silently returns untranslated text as
though it had translated it.
"""

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import page_capture_checks
import page_turns

# Deliberately not adjacent to the API's port (8600), so a person reading a
# port number in a browser URL bar can tell which of the two they are looking at.
DEFAULT_PORT = 8756
TOKEN_HEADER = "X-Baihe-Token"
TOKEN_FILENAME = "extension_token.txt"

MAX_BODY_BYTES = 64 * 1024 * 1024      # whole request, base64 included
MAX_IMAGE_BYTES = 12 * 1024 * 1024     # one decoded image
MAX_IMAGES_PER_REQUEST = 12            # a spread or one visible strip
MAX_TEXT_CHARS = 20000                 # a generous chapter's worth of prose
REQUEST_TIMEOUT_SECONDS = 120.0

# standalone_translate only ever translates one side of a pair
# with English -- see its own docstring -- so /text is bound to the same
# assumption rather than accepting an arbitrary language pair it can't
# actually serve.
ALLOWED_TEXT_LANGUAGES = {"zh", "ja", "ko", "en"}

PIPELINE_LOCK = threading.Lock()

_server_lock = threading.Lock()
_server_started = False
_server = None          # the running ThreadingHTTPServer, for stop_server()
# Bumped by every start and stop, so a server thread still binding when
# stop_server() runs knows not to serve, and a failed old start can't mark a
# newer one as stopped.
_server_generation = 0

_config_lock = threading.Lock()
_config = {
    "engine": None,            # engine name, e.g. "claude"; None = OCR only
    "model": None,             # model key for that engine; None = its default
    "api_key": "",
    "free_tier": False,
    "base_url": None,
    "hf_token": None,
    "tesseract_cmd": None,
    "ocr_backend": None,       # None = auto, by source language
    "prefer_paddle_vl_manga": False,
    "detect_backend": "auto",
}


class EndpointError(Exception):
    """A request this endpoint refuses, with the HTTP status to answer.

    Carries only text this module wrote itself -- never an echo of a
    header value, so a token can't be reflected back into a response.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# -- the token ---------------------------------------------------------
def token_path() -> str:
    """`db.LIBRARY_DIR` is re-read on every call, never captured at
    import time, because `db.configure_library_dir()` rebinds it (tests
    rely on that, and so does portable mode)."""
    import db
    return os.path.join(db.LIBRARY_DIR, TOKEN_FILENAME)


def load_or_create_token() -> str:
    """The endpoint's own shared secret, generated once into the library
    folder. Not an API key and not stored in the database: it only ever
    proves "the thing calling me is the extension this person pasted the
    token into"."""
    path = token_path()
    try:
        with open(path, encoding="utf-8") as fh:
            existing = fh.read().strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass        # a filesystem without POSIX modes (Windows) is fine
    return token


def _token_matches(sent: str, expected: str) -> bool:
    """Constant-time compare. A non-ASCII header value can't be a token
    this module generated, so it's rejected without trying to encode it
    into a comparison."""
    if not sent or not expected:
        return False
    try:
        return secrets.compare_digest(sent.encode("ascii"), expected.encode("ascii"))
    except UnicodeEncodeError:
        return False


# -- configuration pushed in from the UI -------------------------------
# Optional callable returning config overrides, read on every request. The
# API registers one (services/extension_service.py) that resolves the saved
# engine and its key from .env server-side, so a key saved in Settings after
# startup is picked up on the next page without another push.
_config_provider = None


def set_translation_config(**kwargs):
    """Sets the base config the provider's values are merged over; called
    by services/extension_service.push_translation_config (when the server
    starts and when the extension's engine setting changes).
    Unknown keys are ignored rather than raising, so adding a config field
    can't break a running server."""
    with _config_lock:
        for key, value in kwargs.items():
            if key in _config:
                _config[key] = value


def set_config_provider(provider):
    """Registers (or, with None, removes) the per-request config provider.
    Its dict is merged over the pushed config; unknown keys are ignored."""
    global _config_provider
    with _config_lock:
        _config_provider = provider


def get_translation_config() -> dict:
    with _config_lock:
        config = dict(_config)
        provider = _config_provider
    if provider is not None:
        try:
            overrides = provider() or {}
        except Exception:
            overrides = {}      # a broken provider keeps the pushed config
        config.update({k: v for k, v in overrides.items() if k in config})
    return config


def _no_engine_note(config, captured: str) -> list:
    """The warning for a page answered without translation: names the
    engine when one is chosen but has no key, never the key itself."""
    name = (config.get("engine") or "").strip()
    if name and (config.get("api_key") or "").strip():
        return ["warning", f"the extension's translation engine ({name}) couldn't be started, "
                           f"so only the original text was {captured}"]
    if name:
        return ["warning", f"the extension's translation engine ({name}) has no key saved in "
                           f"Settings, so only the original text was {captured}"]
    return ["warning", "no translation engine is configured in Settings, so "
                       f"only the original text was {captured}"]


def _build_engine(config):
    """The engine, or None when nothing is configured. Built per request
    rather than cached, so changing the key in Settings takes effect on
    the next page without a restart."""
    name = (config.get("engine") or "").strip()
    if not name:
        return None
    if not (config.get("api_key") or "").strip() and name != "ollama":
        return None
    import translate_engines
    try:
        return translate_engines.get_engine(
            name, config.get("api_key") or "",
            model=config.get("model") or None,
            free_tier=bool(config.get("free_tier")) and name == "gemini",
            base_url=config.get("base_url") if name == "ollama" else None)
    except Exception:
        # "Is an engine available?" is also what /health answers, so an
        # engine name this build doesn't know must read as "none
        # configured" rather than failing the health check itself.
        return None


# -- the work ----------------------------------------------------------
def _page_source_language(drama, requested):
    return ((drama or {}).get("source_language")
            or (requested or "").strip() or "zh")


def _store_page(drama_id: int, data: bytes, ext: str):
    """Lands the image in the drama through the same import path
    Scanlate's own upload uses, and returns its new page row."""
    import db
    from sources import pipeline
    # The id comes from the import itself: other writers add pages without
    # this module's lock, so "the last page" could be theirs, and a rollback
    # of it would delete their page.
    ids = []
    added = pipeline.add_page_images(drama_id, [(data, ext)], ids_out=ids)
    if not added or not ids:
        raise EndpointError(500, "the image could not be saved as a page")
    return db.get_page(ids[0], drama_id)


def _translate_missing(saved: list, page_id: int, drama: dict, drama_id, source_url: str,
                       config: dict) -> list:
    """Fills only the untranslated bubbles of a stored page. Returns notes."""
    import db
    import translate_engines

    missing = [b for b in saved if not (b.get("translated_text") or "").strip()]
    if not missing:
        return []
    engine = _build_engine(config)
    if engine is None:
        return [["warning", "already in the library; translate it in Scanlate"]]
    glossary = (db.list_glossary_terms(drama["series_id"])
                if drama and drama.get("series_id") else None)
    originals = page_capture_checks.snapshot_texts(missing)
    try:
        page_turns.translate_in_chain(drama_id, source_url, missing, engine, drama, glossary,
                                      _usage_cb(drama, config, engine))
    except Exception as e:
        return [["warning", f"translation failed ({translate_engines.redact_secrets(str(e))})"]]
    return page_capture_checks.save_filled_translations(page_id, missing, originals)


def translate_image(data: bytes, content_type: str, drama_id=None,
                    source_url: str = "", source_language: str = "",
                    store: bool = True) -> dict:
    """One image, end to end: detect + OCR + translate, optionally stored
    into a drama first so the library keeps the real page and its
    bubbles.

    Detection and OCR run under `_PIPELINE_LOCK` -- see this module's
    docstring on `scanlate`'s unlocked model caches. Translation does not.
    """
    import db
    import scanlate
    import translate_engines

    if not data:
        raise EndpointError(400, "the image was empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise EndpointError(413, f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)}MB")
    ext, refusal = page_capture_checks.sniff_image(data)
    if refusal:
        raise EndpointError(*refusal)
    data, ext = page_capture_checks.upright(data, ext)
    drama = db.get_drama(int(drama_id)) if drama_id else None
    if drama_id and drama is None:
        raise EndpointError(404, "no such drama")

    config = get_translation_config()
    notes = []
    page = None
    newly_stored = False
    reused_rev = 0
    temp_path = None

    reuse_saved = None
    busy = None
    claim = None
    try:
        # The discard runs before the lock is released, while no other
        # request can have seen the page.
        with PIPELINE_LOCK, page_turns.discard_on_failure(drama_id) as added:
            if store and drama is not None:
                page = page_capture_checks.page_with_same_bytes(int(drama_id), data)
                if page is not None:
                    # Re-reading would replace bubbles the person corrected in
                    # Scanlate; a page without bubbles has nothing to lose.
                    # Capture sends no chapter labels, so an identical page
                    # shared by two chapters (credits) is reused, not added.
                    # Rev before bubbles: a write in between fails the rev check.
                    reused_rev = int((db.get_page(page["id"]) or {}).get("rev") or 0)
                    reuse_saved = db.load_bubbles(page["id"]) or None
                if reuse_saved is None:
                    newly_stored = page is None
                    if newly_stored:
                        page = _store_page(int(drama_id), data, ext)
                        added.append(page["id"])
                    image_path = os.path.join(db.drama_dir(int(drama_id)), page["filename"])
                # Claimed under the lock, where a re-capture looks the page up.
                busy, mine = page_turns.claim_page(page["id"])
                claim = (page["id"], mine) if mine else None
            else:
                # Overlay-only: the person is reading, not importing, so the
                # bytes never enter the library. Cleaned up below whatever
                # happens, since the pipeline reads it several times.
                import tempfile
                fd, temp_path = tempfile.mkstemp(suffix=ext, prefix="baihe_page_")
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                image_path = temp_path
                if store and drama is None:
                    notes.append(["warning", "no drama chosen, so this page was not saved"])

            if reuse_saved is None and busy is None:
                try:
                    lang = _page_source_language(drama, source_language)
                    bubbles, detect_notes = scanlate.detect_and_ocr_page(
                        image_path, lang,
                        detect_backend=config.get("detect_backend") or "auto",
                        hf_token=config.get("hf_token") or None,
                        ocr_backend=config.get("ocr_backend") or None,
                        tesseract_cmd=config.get("tesseract_cmd") or None,
                        prefer_paddle_vl_manga=bool(config.get("prefer_paddle_vl_manga")),
                        page_id=(page or {}).get("id"))
                finally:
                    if temp_path:
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass

                # Saved before release so a re-capture or Redo finds the read.
                if page is not None:
                    read_notes = page_capture_checks.save_read_bubbles(
                        page["id"], bubbles, newly_stored, reused_rev)
                    stored_bubbles = [] if read_notes else db.load_bubbles(page["id"])

        # Only detection and OCR touch the shared model caches; an LLM call
        # under the lock would stall every other page and a Cancel. From here
        # on a failure keeps the page: a re-capture fills what is missing.
        if busy is not None:
            # Byte-identical, so the other capture's read stands for this one
            # even when it found no text.
            reuse_saved, mine = page_turns.await_other_capture(page["id"], busy)
            if mine is None:
                return page_capture_checks.reused_page_response(
                    data, reuse_saved, [page_turns.STILL_TRANSLATING_NOTE],
                    int(drama_id), page["id"])
            claim = (page["id"], mine)
        if reuse_saved is not None:
            reuse_notes = _translate_missing(reuse_saved, page["id"], drama, drama_id, source_url, config)
            return page_capture_checks.reused_page_response(
                data, reuse_saved, reuse_notes, int(drama_id), page["id"])

        # Detector/OCR notes can quote a Hugging Face download error;
        # the saved HF token now reaches that call, so redact them.
        notes.extend([[n[0], translate_engines.redact_secrets(str(n[1]))]
                      for n in (detect_notes or [])])
        engine = _build_engine(config)
        if bubbles and engine is not None:
            glossary = (db.list_glossary_terms(drama["series_id"])
                        if drama and drama.get("series_id") else None)
            try:
                page_turns.translate_in_chain(drama_id, source_url, bubbles, engine, drama,
                                              glossary, _usage_cb(drama, config, engine))
            except Exception as e:
                # The OCR text is still real and still useful, so it
                # is returned rather than thrown away -- the same
                # choice the Scanlate pipeline makes on this failure.
                notes.append(["warning", f"translation failed ({translate_engines.redact_secrets(str(e))}); "
                                         "the source text below was still read"])
        elif bubbles and engine is None:
            notes.append(_no_engine_note(config, "read"))
        if page is not None:
            notes.extend(read_notes)
            notes.extend(page_capture_checks.save_translations_of_read(
                page["id"], stored_bubbles, bubbles))
    finally:
        if claim is not None:
            page_turns.release_page(*claim)

    width, height = page_capture_checks.image_size(data)
    return {
        "width": width, "height": height,
        "regions": page_capture_checks.regions_for_response(bubbles),
        "notes": notes,
        "drama_id": int(drama_id) if drama_id else None,
        "page_id": (page or {}).get("id"),
        "stored": page is not None,
    }


def translate_text_block(text: str, source_language: str, target_language: str,
                         store: bool = True, user_id=None) -> dict:
    """A raw block of page text (text-capture mode), run through
    the same `translate_engines.standalone_translate` pipeline as the
    app's Standalone translate tool -- reused exactly, not reimplemented,
    per this module's own "one pipeline" rule. This function is the text
    equivalent of `translate_image`: it does not detect or OCR anything,
    since the extension already sends real text rather than pixels, so it
    goes straight to translation.

    Uses the same globally-configured engine as the image routes (pushed
    in from Settings -> Browser extension); the extension itself picks no
    engine and holds no key of its own.
    """
    import db
    import translate_engines

    config = get_translation_config()
    notes = []
    engine = _build_engine(config)
    if engine is None:
        notes.append(_no_engine_note(config, "captured"))
        return {"source_text": text, "translated_text": "", "engine": None,
                "source_language": source_language, "target_language": target_language,
                "notes": notes, "saved_to_history": False}

    ok, message = translate_engines.standalone_direction_support(
        engine.name, source_language, target_language)
    if not ok:
        raise EndpointError(422, message)
    if message:
        notes.append(["warning", message])

    translated_text = ""
    try:
        # Cap and usage log apply here too.
        from services import translate_service
        translated_text, stopped = translate_service.translate_metered(
            engine, config["engine"], text, source_language, target_language)
        if stopped:
            notes.append(["warning", stopped])
    except translate_engines.UnsupportedDirectionError as e:
        raise EndpointError(422, str(e)) from None
    except Exception as e:
        # The captured text is still real and still useful, so it is
        # returned rather than thrown away -- the same choice
        # translate_image makes on this failure.
        notes.append(["warning", f"translation failed ({translate_engines.redact_secrets(str(e))}); "
                                 "the captured text below was still read"])

    saved = False
    if store and translated_text:
        db.save_translate_history(source_language, target_language,
                                  config.get("engine") or "unknown", text, translated_text,
                                  user_id=user_id)
        saved = True

    return {
        "source_text": text,
        "translated_text": translated_text,
        "engine": config.get("engine"),
        "source_language": source_language,
        "target_language": target_language,
        "notes": notes,
        "saved_to_history": saved,
    }


def _usage_cb(drama, config, engine):
    """Cost logging, matching the Scanlate run's own usage_cb. Skipped with
    no drama to attribute it to, rather than inventing a row."""
    if not drama:
        return None
    from engine_backends import paid_calls
    name = config.get("engine") or "unknown"
    return paid_calls.usage_logger(drama["id"], name, engine, "extension_translate")


# -- HTTP --------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    server_version = "BaihePageServer/1.0"
    protocol_version = "HTTP/1.1"
    # socketserver reads this per connection: without it a peer that
    # opens a socket and then stops talking holds a worker thread for
    # ever, which is the standing "a hung peer must never leave work
    # stuck" rule applied to the server side.
    timeout = REQUEST_TIMEOUT_SECONDS

    # -- helpers -------------------------------------------------------
    def _client_is_local(self) -> bool:
        host = (self.client_address[0] if self.client_address else "") or ""
        # ::ffff:127.0.0.1 is the IPv4-mapped form a dual-stack socket
        # reports; anything else is not this machine.
        return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _check_access(self):
        if self.server is not _server:
            # A keep-alive connection accepted before stop_server() would
            # otherwise go on serving after the bridge was turned off.
            raise EndpointError(503, "the extension bridge is turned off: switch on 'Extension bridge' in Baihe's Settings > Browser extension")
        if not self._client_is_local():
            raise EndpointError(403, "this endpoint only answers requests from this computer")
        if not _token_matches(self.headers.get(TOKEN_HEADER, ""), load_or_create_token()):
            raise EndpointError(401, "a valid token header is required; copy it from Settings")

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise EndpointError(400, "a valid Content-Length is required") from None
        if length <= 0:
            raise EndpointError(400, "the request body was empty")
        if length > MAX_BODY_BYTES:
            raise EndpointError(413, f"request is larger than "
                                     f"{MAX_BODY_BYTES // (1024 * 1024)}MB")
        body = self.rfile.read(length)
        if len(body) != length:
            raise EndpointError(400, "the request body was shorter than its Content-Length")
        return body

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Deliberately no Access-Control-Allow-Origin: see this module's
        # docstring. A page that could read this could drive the app.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, error):
        # Keep-alive is on (HTTP/1.1), and a refusal usually happens
        # *before* the request body has been read -- an unauthenticated
        # POST is rejected on its headers alone. Leaving that body in the
        # socket would desync the connection: the next request on it
        # would be parsed starting mid-body. Closing is both simpler and
        # safer than reading megabytes we've already decided to refuse.
        self.close_connection = True
        self._send_json(error.status, {"error": error.message})

    # -- routes --------------------------------------------------------
    def do_GET(self):
        import bridge_requests
        try:
            self._check_access()
            if self.path.split("?")[0] != "/health":
                raise EndpointError(404, "unknown endpoint")
            self._send_json(200, bridge_requests.health_payload())
        except EndpointError as e:
            self._fail(e)
        except Exception:
            self._fail(EndpointError(500, "the app failed to answer this request"))

    def do_POST(self):
        import bridge_requests
        try:
            self._check_access()
            route = self.path.split("?")[0]
            if route not in ("/page", "/pages", "/text", "/novel", "/title"):
                raise EndpointError(404, "unknown endpoint")
            payload = bridge_requests.parse_json(self._read_body())
            if route == "/title":
                from services import extension_title_service as titles
                self._send_json(200, titles.resolve_from_bridge(payload))
                return
            if route in ("/text", "/novel"):
                from services import extension_novel_service as novel
                run = bridge_requests.run_text if route == "/text" else novel.save_from_bridge
                self._send_json(200, run(payload))
                return
            images = payload.get("images")
            bridge_requests.validate_image_batch(route == "/page", images)
            self._send_json(200, bridge_requests.run_images(payload, images))
        except EndpointError as e:
            self._fail(e)
        except Exception:
            # Never surface a raw traceback: it can carry paths, and a
            # header value could reach a log through it.
            self._fail(EndpointError(500, "the app failed to answer this request"))

    def do_OPTIONS(self):
        # No preflight is answered, on purpose. Without one, no web page
        # can ever get the browser's permission to send this endpoint's
        # token header cross-origin -- which is what stops any open tab
        # from driving the app. The extension's service worker does not
        # need a preflight, because `host_permissions` covers it.
        self._fail(EndpointError(405, "cross-origin requests are not accepted"))

    # -- logging -------------------------------------------------------
    def log_message(self, fmt, *args):
        """Routed through the app's own logger, and deliberately without
        the request line: a path is the one place a caller could put a
        token, and this repo's rule is that nothing token-shaped reaches
        a log. Only the method, status and peer are recorded."""
        try:
            from applog import get_logger
            get_logger().info("page_server %s %s from %s",
                              getattr(self, "command", "?"),
                              args[1] if len(args) > 1 else "?",
                              self.client_address[0] if self.client_address else "?")
        except Exception:
            pass

    def log_error(self, fmt, *args):
        self.log_message(fmt, *args)


def serve(port: int, generation=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.timeout = REQUEST_TIMEOUT_SECONDS
    # Daemon handler threads are not joined by server_close(), so stopping
    # never waits on an in-flight OCR run.
    server.daemon_threads = True
    global _server
    with _server_lock:
        if generation is not None and generation != _server_generation:
            server.server_close()
            return
        _server = server
    server.serve_forever()


def ensure_server_started(port: int = DEFAULT_PORT) -> bool:
    """Starts the endpoint once per process, on a daemon thread -- the same
    shape `sources/chapter_check.py`'s `ensure_scheduler_started` uses, and
    idempotent, so a repeat call is safe.

    Returns True if this call started it.
    """
    global _server_started, _server_generation
    with _server_lock:
        if _server_started:
            return False
        _server_started = True
        _server_generation += 1
        generation = _server_generation

    def run():
        global _server_started
        try:
            serve(port, generation=generation)
        except Exception as e:
            with _server_lock:
                if generation == _server_generation:
                    _server_started = False
            try:
                from applog import get_logger
                get_logger().warning("page_server could not start on port %s: %s", port, e)
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name="page-server").start()
    # A moment to let a port conflict surface, so Settings can report it
    # rather than claiming a server that immediately died is running.
    time.sleep(0.05)
    return True


def stop_server() -> bool:
    """Stops the endpoint if it's running (the app's clean shutdown,
    services/shutdown_service.py, and turning the bridge off). Call it from
    any thread but the server's own: shutdown() waits for serve_forever()
    to return. True if it stopped one."""
    global _server, _server_started, _server_generation
    with _server_lock:
        server, _server = _server, None
        _server_started = False
        _server_generation += 1
    if server is None:
        return False
    server.shutdown()
    server.server_close()
    return True


def server_running() -> bool:
    with _server_lock:
        return _server_started
