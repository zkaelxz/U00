"""
page_server.py -- the small, localhost-only HTTP endpoint the browser
extension talks to.

**Why this exists at all.** The adapters in `sources/` do bulk import:
they fetch a chapter, track new ones, and build an offline library. This
is the other half -- "translate the page I am looking at right now" --
and it reaches content the adapters structurally can't, *without this app
ever touching a protection mechanism*. On a site whose pages are
tile-scrambled (mangaz), delivered as `blob:` objects that only exist
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
racing. This is a single-user local app, so serialising is the right
trade rather than a bug.

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

# Only formats the pipeline can actually read (PIL/cv2), mapped to the
# extension `sources.pipeline.add_page_images` expects.
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}

PIPELINE_LOCK = threading.Lock()

_server_lock = threading.Lock()
_server_started = False
_server_port = None
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

# Rolling per-drama translation context, so consecutive pages of the same
# book read as one conversation rather than N isolated pages -- the same
# `previous_context` the Scanlate run (services/scanlate_run_service.py)
# threads between pages. In-memory only, like `background_jobs`: a process
# restart simply starts the context fresh, which costs quality on one page
# and nothing else.
_context_lock = threading.Lock()
_contexts = {}


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


def _translate_missing(saved: list, drama: dict, drama_id, source_url: str, config: dict) -> list:
    """Fills only the untranslated bubbles of a stored page, written by
    bubble id so no existing translation is overwritten. Returns notes."""
    import db
    import scanlate
    import translate_engines

    missing = [b for b in saved if not (b.get("translated_text") or "").strip()]
    if not missing:
        return []
    engine = _build_engine(config)
    if engine is None:
        return [["warning", "already in the library; translate it in Scanlate"]]
    glossary = (db.list_glossary_terms(drama["series_id"])
                if drama and drama.get("series_id") else None)
    key = str(drama_id) if drama_id else f"url:{source_url}"
    with _context_lock:
        previous = _contexts.get(key, "")
    try:
        new_context = scanlate.translate_page_bubbles(
            missing, engine, drama or {}, previous_context=previous,
            glossary_terms=glossary, usage_cb=_usage_cb(drama, config, engine))
    except Exception as e:
        return [["warning", f"translation failed ({translate_engines.redact_secrets(str(e))})"]]
    with _context_lock:
        _contexts[key] = new_context
    for b in missing:
        if (b.get("translated_text") or "").strip():
            db.update_bubble_text(b["id"], b["translated_text"])
    return []


def translate_image(data: bytes, content_type: str, drama_id=None,
                    source_url: str = "", source_language: str = "",
                    store: bool = True) -> dict:
    """One image, end to end: detect + OCR + translate, optionally stored
    into a drama first so the library keeps the real page and its
    bubbles.

    Runs under `_PIPELINE_LOCK` -- see this module's docstring on
    `scanlate`'s unlocked model caches.
    """
    import db
    import scanlate
    import translate_engines

    ext = ALLOWED_IMAGE_TYPES.get((content_type or "").lower().split(";")[0].strip())
    if ext is None:
        raise EndpointError(415, "only PNG, JPEG and WebP images are accepted")
    if not data:
        raise EndpointError(400, "the image was empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise EndpointError(413, f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)}MB")

    drama = db.get_drama(int(drama_id)) if drama_id else None
    if drama_id and drama is None:
        raise EndpointError(404, "no such drama")

    config = get_translation_config()
    notes = []
    page = None
    newly_stored = False
    temp_path = None

    with PIPELINE_LOCK:
        if store and drama is not None:
            page = page_capture_checks.page_with_same_bytes(int(drama_id), data)
            if page is not None:
                # Re-reading would replace bubbles the person corrected in
                # Scanlate; a page without bubbles has nothing to lose.
                # Capture sends no chapter labels, so an identical page
                # shared by two chapters (credits) is reused, not added.
                saved = db.load_bubbles(page["id"])
                if saved:
                    reuse_notes = _translate_missing(saved, drama, drama_id, source_url, config)
                    width, height = page_capture_checks.image_size(data)
                    return {
                        "width": width, "height": height,
                        "regions": page_capture_checks.regions_for_response(saved),
                        "notes": reuse_notes, "drama_id": int(drama_id),
                        "page_id": page["id"], "stored": True,
                        "already_stored": True,
                    }
            newly_stored = page is None
            if newly_stored:
                page = _store_page(int(drama_id), data, ext)
            image_path = os.path.join(db.drama_dir(int(drama_id)), page["filename"])
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
            # Detector/OCR notes can quote a Hugging Face download error;
            # the saved HF token now reaches that call, so redact them.
            notes.extend([[n[0], translate_engines.redact_secrets(str(n[1]))]
                          for n in (detect_notes or [])])

            engine = _build_engine(config)
            if bubbles and engine is not None:
                glossary = (db.list_glossary_terms(drama["series_id"])
                            if drama and drama.get("series_id") else None)
                key = str(drama_id) if drama_id else f"url:{source_url}"
                with _context_lock:
                    previous = _contexts.get(key, "")
                try:
                    new_context = scanlate.translate_page_bubbles(
                        bubbles, engine, drama or {}, previous_context=previous,
                        glossary_terms=glossary, usage_cb=_usage_cb(drama, config, engine))
                    with _context_lock:
                        _contexts[key] = new_context
                except Exception as e:
                    # The OCR text is still real and still useful, so it
                    # is returned rather than thrown away -- the same
                    # choice the Scanlate pipeline makes on this failure.
                    notes.append(["warning", f"translation failed ({translate_engines.redact_secrets(str(e))}); "
                                             "the source text below was still read"])
            elif bubbles and engine is None:
                notes.append(_no_engine_note(config, "read"))

            if page is not None:
                db.save_bubbles(page["id"], bubbles)
        except BaseException:
            # A page whose reading failed must not stay behind as an empty
            # page: the caller reports it as not delivered, and a retry
            # would add it a second time.
            if page is not None and newly_stored:
                from sources import pipeline
                try:
                    pipeline._discard_pages(int(drama_id), [page["id"]])
                except Exception:
                    pass
            raise
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

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
                         store: bool = True) -> dict:
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
        translated_text = translate_engines.standalone_translate(
            text, engine, source_language, target_language)
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
                                  config.get("engine") or "unknown", text, translated_text)
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
    import db
    import translate_engines
    name = config.get("engine") or "unknown"

    def log(inp, out):
        db.log_usage(drama["id"], name, getattr(engine, "model", name),
                     "extension_translate", inp, out,
                     translate_engines.estimate_cost_for_engine(engine, inp, out))
    return log


def select_page_images(images, page_url: str):
    """Which of the sent images are real pages, decided by
    `sources/generic_import.py`'s existing filter -- size floor, aspect
    and width clustering, duplicate and third-party rejection -- rather
    than by a second implementation in JavaScript that would drift from
    it.

    `images`: list of dicts with `url`, `content` and `content_type`.
    Returns `(kept, rejected)`, each a list of `(image, reason)` pairs
    where `reason` is `""` for kept ones.
    """
    from sources import generic_import
    candidates = []
    for order, image in enumerate(images):
        c = generic_import.ImageCandidate(image.get("url") or page_url, order)
        c.content = image.get("content") or b""
        generic_import.measure(c)
        candidates.append(c)
    kept, rejected = generic_import.filter_page_images(candidates, page_url)
    kept_set = {c.order for c in kept}
    return ([images[c.order] for c in kept],
            [(images[c.order], c.reject_reason) for c in rejected
             if c.order not in kept_set])


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
        try:
            self._check_access()
            if self.path.split("?")[0] != "/health":
                raise EndpointError(404, "unknown endpoint")
            import db
            self._send_json(200, {
                "ok": True,
                "app": "Baihe Subtitler",
                "engine_configured": _build_engine(get_translation_config()) is not None,
                # The extension batches a whole chapter to this, so the
                # number is stated once, here, and cannot drift from its copy.
                "max_images_per_request": MAX_IMAGES_PER_REQUEST,
                # `title_en or title_zh` is the app's own display-title
                # convention, not a new one.
                "dramas": [{"id": d["id"],
                            "title": (d.get("title_en") or d.get("title_zh")
                                      or f"drama #{d['id']}"),
                            "media_type": d.get("media_type") or "",
                            "source_language": d.get("source_language") or ""}
                           for d in db.list_dramas()],
            })
        except EndpointError as e:
            self._fail(e)
        except Exception:
            self._fail(EndpointError(500, "the app failed to answer this request"))

    def do_POST(self):
        try:
            self._check_access()
            route = self.path.split("?")[0]
            if route not in ("/page", "/pages", "/text"):
                raise EndpointError(404, "unknown endpoint")
            payload = self._parse_json(self._read_body())
            if route == "/text":
                self._send_json(200, self._run_text(payload))
                return
            images = payload.get("images")
            if not isinstance(images, list) or not images:
                raise EndpointError(400, "at least one image is required")
            if route == "/page" and len(images) != 1:
                raise EndpointError(400, "/page takes exactly one image; use /pages for more")
            if len(images) > MAX_IMAGES_PER_REQUEST:
                raise EndpointError(413, f"at most {MAX_IMAGES_PER_REQUEST} images per request")
            self._send_json(200, self._run(payload, images))
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

    def _parse_json(self, body: bytes) -> dict:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EndpointError(400, "the request body was not valid JSON") from None
        if not isinstance(payload, dict):
            raise EndpointError(400, "the request body must be a JSON object")
        return payload

    def _run_text(self, payload) -> dict:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise EndpointError(400, "text is required")
        if len(text) > MAX_TEXT_CHARS:
            raise EndpointError(413, f"text is longer than {MAX_TEXT_CHARS} characters")
        source_language = str(payload.get("source_language") or "zh")
        target_language = str(payload.get("target_language") or "en")
        if (source_language not in ALLOWED_TEXT_LANGUAGES
                or target_language not in ALLOWED_TEXT_LANGUAGES):
            raise EndpointError(400, "source/target language must be one of zh, ja, ko, en")
        if source_language == target_language:
            raise EndpointError(400, "source and target language must differ")
        if "en" not in (source_language, target_language):
            raise EndpointError(400, "one of source/target language must be English -- the same "
                                     "limit the Translate page has")
        store = bool(payload.get("store", True))
        return translate_text_block(text, source_language, target_language, store=store)

    def _run(self, payload, images) -> dict:
        import base64
        drama_id = payload.get("drama_id")
        store = bool(payload.get("store", True))
        source_url = str(payload.get("source_url") or "")
        source_language = str(payload.get("source_language") or "")
        decoded = []
        for image in images:
            if not isinstance(image, dict):
                raise EndpointError(400, "each image must be a JSON object")
            # Checked before decoding so an oversized payload never allocates
            # the full decoded copy.
            data = image.get("data") or ""
            if isinstance(data, (str, bytes)) and len(data) * 3 // 4 > MAX_IMAGE_BYTES:
                raise EndpointError(413, f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)}MB")
            try:
                content = base64.b64decode(data, validate=True)
            except Exception:
                raise EndpointError(400, "an image's data was not valid base64") from None
            decoded.append({
                "key": str(image.get("key") or ""),
                # A `data:` or `blob:` image's "URL" is the image itself,
                # which would otherwise be echoed back whole in every
                # response (a real 12-image send measured megabytes of
                # pure echo). It's only ever shown to a person, so it is
                # capped here rather than carried in full.
                "url": page_capture_checks.short_url(image.get("url")),
                "content_type": str(image.get("content_type") or ""),
                "content": content,
            })

        # Let the existing page-image filter decide what is a real page,
        # but only when there is a set worth clustering: filtering a
        # deliberate single "translate this one" send would just refuse
        # the very image the person picked.
        skipped = []
        if len(decoded) > 1 and payload.get("filter_pages", True):
            decoded, rejected = select_page_images(decoded, source_url)
            skipped = [{"key": image["key"], "url": image["url"], "reason": reason}
                       for image, reason in rejected]
            if not decoded:
                raise EndpointError(422, "none of those images look like comic pages")

        # One bad page must not discard the rest of a chapter: each failure
        # is named so the person sees a mismatch instead of a short count.
        results = []
        failed = []
        stopped = ""
        for image in decoded:
            try:
                if page_capture_checks.looks_blank(image["content"]):
                    raise EndpointError(422, "the page was blank (the reader had not drawn it yet)")
                result = translate_image(
                    image["content"], image["content_type"], drama_id=drama_id,
                    source_url=source_url, source_language=source_language, store=store)
            except EndpointError as e:
                if len(decoded) == 1 and e.status in (404, 413, 415):
                    raise
                failed.append({"key": image["key"], "url": image["url"], "error": e.message})
                continue
            except Exception as e:
                # The raw text can carry the OS user name or library path
                # and the extension shows it on the page being read, so
                # only a fixed message leaves; the detail stays in the log.
                page_capture_checks.log_page_failure(e)
                failed.append({"key": image["key"], "url": image["url"],
                               "error": page_capture_checks.page_failure_message(e)})
                if page_capture_checks.is_request_fatal(e):
                    # Every remaining page would fail the same way and
                    # each would leave a stored, empty page behind.
                    stopped = page_capture_checks.page_failure_message(e)
                    break
                continue
            result["key"] = image["key"]
            result["url"] = image["url"]
            results.append(result)
        return {"pages": results, "skipped": skipped, "failed": failed,
                "received": len(images),
                "stored": sum(1 for r in results if r.get("stored")),
                "already_stored": sum(1 for r in results if r.get("already_stored")),
                **({"stopped": stopped} if stopped else {})}

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
    global _server_port, _server
    with _server_lock:
        if generation is not None and generation != _server_generation:
            server.server_close()
            return
        _server_port = port
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


def server_port():
    return _server_port
