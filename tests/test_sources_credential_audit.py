"""Step 110: credential handling in the content sources (audit note:
docs/sources-credential-audit.md).

Static: nothing in the sources layer reads a browser profile's cookies or
storage state, `.cookies` is read only where the audit allows it, and no
store/health/log call is handed request headers or cookies.

Behavioural: a request carrying Cookie / Authorization headers, answered
with Set-Cookie headers and cookies (200, a challenge, 403, 5xx), through
the paced client with a persistent raw cache, the access ladder, the
capability record and a chapter check, leaves none of those values in any
sources.db table, the raw-cache files, the client's attempts/stats or the
log. Fakes only: no network, no browser."""
import ast
import re
import dataclasses
import json
import logging
import os
from pathlib import Path

import pytest

from services import url_guard
from sources import chapter_check, detect, health, http, ladder, registry, store
from sources.base import SourceAdapter
from sources.cache import RawCache
from sources.http import Response
from sources.models import AccessTier, SourceError

from .sources_helpers import make_client

ROOT = Path(__file__).resolve().parent.parent

REQ_COOKIE = "REQCOOKIESENTINEL7f3a"
AUTH = "AUTHSENTINEL91bc"
SET_COOKIE = "SETCOOKIESENTINEL44de"
TICKET = "TICKETSENTINEL0a1b"
SENTINELS = (REQ_COOKIE, AUTH, SET_COOKIE, TICKET)

# Where `.cookies` may be read -- the exact expression, not the whole
# function (so e.g. `session.cookies`, the process-wide jar http.py warns
# against, is never allowed) -- and why (see the audit note).
COOKIE_READS_ALLOWED = {
    ("sources/http.py", "_requests_transport", "hop.cookies"):
        "builds Response.cookies from each redirect hop's own jar; returned, never stored",
}
# Calls that write to sources.db, the capability record or a log.
SINK_CALLS = {"log_attempt", "record_failure", "record_success", "mark_checked",
              "put_extraction", "save_capabilities", "set_setting", "put", "track_series",
              "record_new_chapters", "remember_images", "save_version", "set_result",
              "update_progress", "update_drama", "dump", "dumps", "write",
              "debug", "info", "warning", "error", "exception", "critical", "print"}
# Names that hold (or wrap) request headers, cookies, a cookie-derived value,
# or a yt-dlp info dict (which can carry a `cookies` field).
SECRET_NAMES = {"headers", "hdrs", "cur_headers", "default_headers", "cookies", "cookie",
                "cookies_file", "cookies_browser", "cookiefile", "cookiesfrombrowser",
                "ticket", "sid", "session_cookie", "raw_metadata", "result_info",
                "__dict__"}
# Wrapping a whole object in one of these would carry Response.headers/cookies.
SECRET_WRAPPERS = {"asdict", "vars"}
# Browser-profile APIs that would read or export a signed-in session.
PROFILE_READS = {"storage_state", "add_cookies", "cookies", "get_cookies", "clear_cookies"}
# Names for a browser profile folder: opening or copying one reads the session.
PROFILE_PATHS = {"profile_dir", "browser_profile_dir", "browser_profiles_root"}


# The API/service consumers of the yt-dlp cookie setting, besides the sources
# layer. The Streamlit ones (tabs/) are not scanned: Streamlit is frozen and
# being deleted by 2026-10-30.
YTDLP_COOKIE_FILES = ("video_download.py", "live_translate.py", "services/url_media_service.py",
                      "services/live_service.py", "services/settings_service.py",
                      "api/routers/settings_routes.py", "api/routers/live_routes.py",
                      "api/routers/media_routes.py")
# Scripts that would read a signed-in session out of a page or a profile.
SESSION_JS = re.compile(r"document\.cookie|localStorage|sessionStorage", re.I)


def _scanned_files():
    files = sorted((ROOT / "sources").rglob("*.py"))
    files += sorted((ROOT / "services").glob("sources_*.py"))
    files += sorted((ROOT / "api" / "routers").glob("sources_*.py"))
    files.append(ROOT / "page_fetch.py")
    files += [ROOT / f for f in YTDLP_COOKIE_FILES]
    return [f for f in files if f.exists()]


def _functions(tree):
    """(node, enclosing function name) for every node in the module."""
    out = []

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                else fn
            out.append((child, name))
            walk(child, name)
    _mark_docstrings(tree)
    walk(tree, None)
    return out


def _is_docstring(node) -> bool:
    # Marked on the node itself (per tree): an id() set would outlive the
    # tree and CPython reuses ids, so a later file's real string could be
    # taken for an old docstring and skipped.
    return getattr(node, "_is_docstring", False)


def _mark_docstrings(tree):
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(n.body[0].value, ast.Constant):
            n.body[0].value._is_docstring = True
    return tree


def _rel(path):
    return path.relative_to(ROOT).as_posix()


def test_no_profile_cookie_or_storage_state_reads():
    bad = []
    for path in _scanned_files():
        for node, fn in _functions(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in PROFILE_READS:
                bad.append(f"{_rel(path)}:{node.lineno} {fn}: .{node.func.attr}(...)")
            if isinstance(node, ast.keyword) and node.arg == "storage_state":
                bad.append(f"{_rel(path)}: storage_state= in {fn}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and SESSION_JS.search(node.value) and not _is_docstring(node):
                bad.append(f"{_rel(path)}:{node.lineno} {fn}: script reads page storage")
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(
                    node.func, "id", "")) in ("open", "connect", "copytree", "copy", "copy2") \
                    and any(isinstance(sub, (ast.Name, ast.Attribute)) and
                            getattr(sub, "id", getattr(sub, "attr", "")) in PROFILE_PATHS
                            for a in node.args for sub in ast.walk(a)):
                bad.append(f"{_rel(path)}:{node.lineno} {fn}: opens/copies a browser profile")
    assert bad == [], "A signed-in browser session must stay in its profile:\n" + "\n".join(bad)


def test_cookie_attribute_reads_only_where_allowed():
    found = set()
    bad = []
    for path in _scanned_files():
        for node, fn in _functions(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "cookies" \
                    and isinstance(node.ctx, ast.Load):
                key = (_rel(path), fn, ast.unparse(node))
                if key in COOKIE_READS_ALLOWED:
                    found.add(key)
                else:
                    bad.append(f"{_rel(path)}:{node.lineno} {fn}: reads .cookies")
    assert bad == [], ("New `.cookies` read: add it to COOKIE_READS_ALLOWED with the reason "
                       "it is never stored or logged, and to the audit note:\n" + "\n".join(bad))
    assert found == set(COOKIE_READS_ALLOWED), "stale COOKIE_READS_ALLOWED entry"


def test_no_headers_or_cookies_handed_to_a_store_or_log_call():
    bad = []
    for path in _scanned_files():
        for node, fn in _functions(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else \
                getattr(node.func, "id", "")
            if name not in SINK_CALLS:
                continue
            for arg in list(node.args) + [k.value for k in node.keywords]:
                for sub in ast.walk(arg):
                    ident = sub.id if isinstance(sub, ast.Name) else \
                        sub.attr if isinstance(sub, ast.Attribute) else None
                    if isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant):
                        ident = sub.slice.value   # result["raw_metadata"]
                    if isinstance(sub, ast.Call) and getattr(
                            sub.func, "attr", getattr(sub.func, "id", "")) in SECRET_WRAPPERS:
                        ident = sub.func.attr if isinstance(sub.func, ast.Attribute) \
                            else sub.func.id
                    if ident in SECRET_NAMES or ident in SECRET_WRAPPERS:
                        bad.append(f"{_rel(path)}:{node.lineno} {fn}: {name}(... {ident} ...)")
    assert bad == [], "Headers/cookies must never reach sources.db or a log:\n" + "\n".join(bad)


def test_attempt_evidence_keeps_no_credential_headers():
    ev = detect.evidence(200, {"Set-Cookie": f"sid={SET_COOKIE}", "Cookie": REQ_COOKIE,
                               "Authorization": AUTH, "Server": "nginx"},
                         "<html><title>t</title></html>", "https://a.example/x")
    assert ev["headers"] == {"server": "nginx"}
    assert not any(s in json.dumps(ev) for s in SENTINELS)


# ---------------------------------------------------------------------------
# Behavioural
# ---------------------------------------------------------------------------

BASE = "https://src.example"


def _resp(status, url, body=b"<html><title>Chapter</title><p>text</p></html>"):
    return Response(status, {"content-type": "text/html; charset=utf-8",
                             "Set-Cookie": f"sid={SET_COOKIE}; Path=/; HttpOnly"},
                    body, url, cookies={"sid": SET_COOKIE, "virgo!__ticket": TICKET})


def _transport(method, url, headers, data, timeout):
    assert timeout
    path = url[len(BASE):]
    if path.startswith("/ok"):
        return _resp(200, url)
    if path.startswith("/challenge"):
        return _resp(403, url, b"<html><title>Just a moment...</title>"
                               b"<p>Checking your browser</p><div id='cf-chl-widget'></div></html>")
    if path.startswith("/forbidden"):
        return _resp(403, url, b"<html><title>403</title></html>")
    return _resp(503, url, b"busy")


def _db_dump() -> str:
    with store.connect() as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        rows = [dict(r) for t in tables for r in conn.execute(f"SELECT * FROM {t}")]
    return json.dumps(rows, ensure_ascii=False, default=str)


def _files_dump(root) -> bytes:
    out = b""
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                out += fh.read()
    return out


@pytest.fixture
def world(isolated_db, monkeypatch, caplog):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])
    caplog.set_level(logging.DEBUG)
    http.reset_pacing_state()
    yield caplog
    http.reset_pacing_state()


def _client():
    client = make_client("credtest", _transport, max_retries=1, backoff_base=0.0)
    client.cache = RawCache("keep_originals")
    client.default_headers.update({"Cookie": f"sid={REQ_COOKIE}",
                                   "Authorization": f"Bearer {AUTH}"})
    return client


def test_credentials_never_land_in_store_cache_attempts_or_logs(world, monkeypatch, capfd):
    caplog = world
    client = _client()

    # the client does see the cookies (the fakes work) ...
    assert client.get(f"{BASE}/ok/0", use_cache=False).cookies["virgo!__ticket"] == TICKET
    # a success (cached), a challenge, a 403 and a 5xx after its retry
    assert client.get(f"{BASE}/ok/1").ok
    assert client.get(f"{BASE}/ok/1").from_cache
    for path in ("/challenge/1", "/forbidden/1", "/busy/1"):
        with pytest.raises(SourceError):
            client.get(f"{BASE}{path}")

    # the access ladder, its log row and the capability record
    for path in ("/ok/2", "/challenge/2"):
        result = ladder.run_ladder(f"{BASE}{path}",
                                   {AccessTier.STATIC_HTTP: ladder.static_tier(client)},
                                   source="credtest")
        ladder.record_ladder_result("credtest", result)

    # a chapter check whose chapter list fails
    health.reset("credtest")
    monkeypatch.setattr(registry, "is_enabled", lambda name: True)

    class Adapter(SourceAdapter):
        name = "credtest"

        def get_chapters(self, series_id):
            self.client.get(f"{BASE}/forbidden/list")

    store.track_series("credtest", "s1", "Series", f"{BASE}/series/s1")
    summary = chapter_check.run_check_cycle(adapter_factory=lambda n: Adapter(client=client))
    assert summary["errors"] and store.list_tracked_series()[0]["last_check_error"]

    # ... and none of them was kept anywhere
    stored = _db_dump()
    assert store.recent_attempts("credtest") and "credtest" in stored
    cached = _files_dump(store.cache_dir())
    kept = json.dumps([dataclasses.asdict(a) for a in client.attempts], default=str) + \
        json.dumps(client.snapshot()) + caplog.text + "".join(capfd.readouterr())
    for s in SENTINELS:
        assert s not in stored, f"{s} in sources.db"
        assert s.encode() not in cached, f"{s} in the raw cache"
        assert s not in kept, f"{s} in attempts/stats/log"


def test_profile_folders_are_apart_from_the_cache_and_confined(isolated_db):
    """Profiles live under their own folder, outside the raw cache, so
    clearing the cache or a backup's cache handling never touches them."""
    prof = os.path.realpath(store.browser_profiles_root())
    cache = os.path.realpath(store.cache_dir())
    assert not prof.startswith(cache + os.sep) and not cache.startswith(prof + os.sep)
    assert os.path.realpath(store.browser_profile_dir("../../etc")).startswith(prof + os.sep)


@pytest.mark.xfail(strict=True, reason=(
    "Audit gap G1 (docs/sources-credential-audit.md): a signed/tokened URL's query is "
    "stored unredacted in source_health.last_error and access_attempts. Not an account "
    "credential and never sent to a remote client, but it belongs in a follow-up step."))
def test_url_tokens_are_not_stored(world):
    client = _client()
    with pytest.raises(SourceError):
        client.get(f"{BASE}/challenge/x?token=URLTOKENSENTINEL5e")
    for path in ("/forbidden/y?sig=URLTOKENSENTINEL5e", "/challenge/z?sig=URLTOKENSENTINEL5e"):
        result = ladder.run_ladder(f"{BASE}{path}",
                                   {AccessTier.STATIC_HTTP: ladder.static_tier(client)},
                                   source="credtest")
        ladder.record_ladder_result("credtest", result)   # source_capabilities too
    health.reset("credtest")
    client.get(f"{BASE}/ok/c?token=URLTOKENSENTINEL5e")    # cache_index.url
    assert "URLTOKENSENTINEL5e" not in _db_dump()
