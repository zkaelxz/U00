"""
services/sources_registry_service.py -- the Sources catalog and status for
the API (Migration Slice 56, S-1 read-only part; S-2 adds the config writes
below). This is NOT the Workspace Source stage (services/source_service.py).

Everything returned is plain dicts. Nothing here returns a proxy URL, a
filesystem path, a signed-in profile's contents, a rule body of a saved
site profile or a query string: URLs are reduced to scheme+host+path and
free text goes through `_scrub` (secrets via translate_engines.redact_secrets,
then paths and URL queries).

ToS/robots enforcement is OFF by user decision (Step 90, 2026-09-29 Q1), so
the recorded `terms` block is information only: nothing here ever says a
source is "permitted".

First-use write: `sources.store.connect()` creates `<library>/sources.db`
and its schema on first use, so even these GETs may create that file (tests
use an isolated library dir via `isolated_db`).
"""

import re
from urllib.parse import urlsplit

import db
from services import ownership_service
from services.service_errors import (InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from sources import auth_browser, cache as src_cache, health, ladder, registry, store
from sources import http as src_http
from sources import profiles as src_profiles
from translate_engines import redact_secrets

_LIGHTS = {health.GREEN: "green", health.YELLOW: "yellow", health.RED: "red"}

# Settings the API exposes (numbers/enums/bools only). http_proxy_url and
# page_server_enabled are deliberately absent: a proxy URL is a secret-ish
# value and an SSRF/exfiltration pivot if a remote client could set it.
SETTING_KEYS = (
    "pace_min_delay", "pace_max_delay", "max_concurrent", "max_retries",
    "session_break_min_requests", "session_break_max_requests",
    "session_break_min_delay", "session_break_max_delay",
    "cache_mode", "cache_max_mb", "check_interval_hours", "auto_queue_new_chapters",
    "demo_source_enabled", "extraction_diagnostics",
)

# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------

_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
_WIN_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>]*")
_UNC_PATH = re.compile(r"\\\\[^\s\"'<>]+")
# A query after a relative request path (requests' "url: /book/7?sig=..."),
# which _URL_IN_TEXT (absolute URLs only) doesn't see.
_REL_QUERY = re.compile(r"(?<=[\w/\]])\?[^\s)'\"<>]+")
_POSIX_PATH = re.compile(r"(?<![\w:/.\-])(?:~|\.{1,2})?/(?:[\w.\-~@+ ]+/)+[\w.\-~@+]*|"
                         r"(?<![\w:/.\-])~/[\w.\-~@+]+")


def safe_url(url) -> str:
    """scheme + host + path only: no query, fragment or userinfo (a source
    URL can carry a signed token). Anything unparsable gives ""."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return ""
    if not parts.scheme or not host:
        return ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


def _scrub(text):
    """Free text safe to show: secrets redacted, URL queries and filesystem
    paths (including the library folder) removed."""
    if text is None:
        return None
    text = str(text)
    lib = str(getattr(db, "LIBRARY_DIR", "") or "")
    if lib:
        text = text.replace(lib, "[path]")
    text = redact_secrets(text)
    text = _URL_IN_TEXT.sub(lambda m: safe_url(m.group(0)) or "[url]", text)
    text = _UNC_PATH.sub("[path]", text)
    text = _WIN_PATH.sub("[path]", text)
    text = _POSIX_PATH.sub("[path]", text)
    text = _REL_QUERY.sub("", text)
    return text


def _scrub_any(value):
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, dict):
        return {str(k): _scrub_any(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_scrub_any(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _visible_classes() -> dict:
    """Adapter classes the API lists. The offline demo source is hidden
    unless switched on, exactly like the Sources tab."""
    out = {}
    for name, cls in registry.adapter_classes().items():
        if getattr(cls, "is_demo", False) and not store.get_setting("demo_source_enabled"):
            continue
        out[name] = cls
    return out


def _require_source(name: str):
    cls = _visible_classes().get(name)
    if cls is None:
        raise NotFoundError("No such source.")
    return cls


def _import_supported(adapter) -> bool:
    return bool(adapter.supports("get_pages") or adapter.supports("get_chapter_text"))


def _health_view(name: str) -> dict:
    h = health.get(name)
    return {
        "light": _LIGHTS.get(health.light(name), "green"),
        "consecutive_failures": h["consecutive_failures"],
        "last_success": h["last_success"],
        "last_failure": h["last_failure"],
        "last_error_type": _scrub(h["last_error_type"]),
        "last_error_category": health.category(h["last_error_type"]),
        "last_error": _scrub(h["last_error"]),
        "last_latency": h["last_latency"],
        "unavailable_until": h["unavailable_until"],
        "retry_after": health.retry_after(name),
    }


def _summary(name: str, cls) -> dict:
    adapter = cls()
    return {
        "name": name,
        "display_name": cls.display_name or name,
        "content_types": list(cls.content_types),
        "languages": list(cls.languages),
        "supports": {m: bool(adapter.supports(m)) for m in
                     ("search", "get_series", "get_chapters", "get_pages", "download_page",
                      "get_chapter_text", "get_audio_url", "login")},
        "import_supported": _import_supported(adapter),
        "auth_supported": bool(cls.auth_supported),
        "supports_adult_toggle": bool(cls.supports_adult_toggle),
        "enabled": registry.is_enabled(name),
        "adult_enabled": bool(cls.supports_adult_toggle and store.adult_enabled(name)),
        "health": _LIGHTS.get(health.light(name), "green"),
        "has_saved_signin": bool(auth_browser.has_profile("", name)),
    }


# ---------------------------------------------------------------------------
# Reads (S-1)
# ---------------------------------------------------------------------------

def list_sources() -> list:
    return [_summary(name, cls) for name, cls in _visible_classes().items()]


def get_source(name: str) -> dict:
    cls = _require_source(name)
    adapter = cls()
    caps = ladder.apply_terms(ladder.load_capabilities(name, adapter.capabilities()))
    d = caps.to_dict()
    tiers = {t: {"tested": bool(r.get("tested")), "ok": bool(r.get("ok")),
                 "reason": r.get("reason"), "detail": _scrub(r.get("detail")), "at": r.get("at")}
             for t, r in d.get("tiers", {}).items()}
    out = _summary(name, cls)
    out.update({
        "status": d["status"],
        "technical_status": d["technical_status"],
        "access_method": d["access_method"],
        "content_access_status": d["content_access_status"],
        # The separate Step 23k fields, never collapsed into one verdict.
        "authentication_required": d["authentication_required"],
        "purchase_required": d["purchase_required"],
        "technical_protection": d["technical_protection"],
        "automation_permission": d["automation_permission"],
        "ai_ml_use": d["ai_ml_use"],
        "tiers": tiers,
        "technical": _scrub_any(d["technical"]),
        # Recorded findings, read-only information. Enforcement is OFF, so
        # this never means "permitted".
        "terms": _scrub_any(d["terms"]),
        "terms_enforced": False,
        "health_detail": _health_view(name),
    })
    return out


def list_attempts(name: str, limit: int = 50) -> list:
    _require_source(name)
    out = []
    for a in store.recent_attempts(name, limit=limit):
        handoff = a.get("handoff") or {}
        out.append({
            "url": safe_url(a.get("url")),
            "created_at": a.get("created_at"),
            "tier": a.get("tier"),
            "test_now": bool(a.get("test_now")),
            "ok": a.get("ok"),
            "technical_status": a.get("technical_status"),
            "capability_status": a.get("capability_status"),
            "reasons": [_scrub(r) for r in (a.get("reasons") or [])],
            "lines": [_scrub(x) for x in (a.get("lines") or [])],
            "handoff": ({"tier": handoff.get("tier"), "reason": _scrub(handoff.get("reason")),
                         "url": safe_url(handoff.get("url"))} if handoff else None),
        })
    return out


def get_settings() -> dict:
    s = store.all_settings()
    out = {k: s[k] for k in SETTING_KEYS}
    out["proxy_configured"] = bool(str(s.get("http_proxy_url") or "").strip())
    out["cache_modes"] = list(src_cache.MODES)
    stats = src_cache.RawCache().stats()
    out["cache"] = {"entries": stats["entries"], "bytes": stats["bytes"]}
    return out


def list_profiles() -> list:
    """Saved per-domain extraction profiles: version summaries only, never
    the rules themselves."""
    out = []
    for domain in src_profiles.list_domains():
        vs = []
        for v in src_profiles.versions(domain):
            fail = v.get("last_failure") or {}
            vs.append({
                "version": v.get("version"), "kind": v.get("kind"), "status": v.get("status"),
                "origin": _scrub(v.get("origin")), "created_at": v.get("created_at"),
                "approved": bool(v.get("approved")), "failures": v.get("failures") or 0,
                "last_failure_reason": _scrub(fail.get("reason")) if fail else None,
                "last_used": v.get("last_used"),
            })
        out.append({"domain": _scrub(domain), "versions": vs})
    return out


def list_tracked(principal=None) -> list:
    """Every tracked series (household-wide by decision). A linked drama
    the principal can't see is reported as `drama_id: None`, so a private
    drama's id doesn't leak (auth B2)."""
    def linked(drama_id):
        if drama_id is None or ownership_service.can_see_drama(principal, drama_id):
            return drama_id
        return None
    return [{"source": r["source"], "series_id": r["series_id"], "title": _scrub(r["title"]),
             "url": safe_url(r.get("url")), "drama_id": linked(r.get("drama_id")),
             "last_checked": r.get("last_checked"),
             "last_check_error": _scrub(r.get("last_check_error"))}
            for r in store.list_tracked_series()]


def list_notifications(include_dismissed: bool = False) -> list:
    return [{"id": r["id"], "source": r["source"], "series_id": r["series_id"],
             "chapter_id": r["chapter_id"], "title": _scrub(r.get("title")),
             "created_at": r["created_at"], "dismissed": bool(r["dismissed"])}
            for r in store.list_notifications(include_dismissed=include_dismissed)]


# ---------------------------------------------------------------------------
# Writes (S-2). All POST at the router; each verifies the source, domain or id
# exists before it changes anything.
# ---------------------------------------------------------------------------

# Same ranges as tabs/sources_tab.py's settings form. http_proxy_url and
# page_server_enabled are NOT settable here (a proxy URL set by a remote
# client is an exfiltration/SSRF pivot; the page server opens a port).
_RANGES = {
    "pace_min_delay": (0.0, 60.0), "pace_max_delay": (0.0, 120.0),
    "max_concurrent": (1, 4), "max_retries": (0, 6),
    "session_break_min_requests": (0, 200), "session_break_max_requests": (0, 200),
    "session_break_min_delay": (0.0, 600.0), "session_break_max_delay": (0.0, 900.0),
    "check_interval_hours": (0, 168),
    "cache_max_mb": (0, 1_000_000),
}
_BOOLS = ("auto_queue_new_chapters", "demo_source_enabled", "extraction_diagnostics")


def _num(key, value):
    lo, hi = _RANGES[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidInputError(f"{key} must be a number.")
    if not lo <= value <= hi:
        raise InvalidInputError(f"{key} must be between {lo} and {hi}.")
    return type(store.DEFAULT_SETTINGS[key])(value)


def set_source_enabled(name: str, enabled: bool) -> dict:
    cls = _require_source(name)
    registry.set_enabled(name, bool(enabled))
    return _summary(name, cls)


def set_adult_enabled(name: str, enabled: bool) -> dict:
    cls = _require_source(name)
    if not cls.supports_adult_toggle:
        raise UnsupportedOperationError("This source has no adult-content switch.")
    store.set_adult_enabled(name, bool(enabled))
    return _summary(name, cls)


def update_settings(changes: dict) -> dict:
    """Partial update of the whitelisted settings. Unknown keys (including
    http_proxy_url and page_server_enabled) are rejected. The pacing floor:
    pace_min_delay may not go below the built-in default (the tab lets a
    local user pick 0; the API does not). reset_pacing_state() runs after
    saving, as the tab does."""
    changes = dict(changes or {})
    if not changes:
        raise InvalidInputError("No settings to change.")
    unknown = sorted(set(changes) - set(SETTING_KEYS))
    if unknown:
        raise InvalidInputError("These settings can't be changed through the API: "
                                + ", ".join(unknown))
    clean = {}
    for k, v in changes.items():
        if k in _RANGES:
            clean[k] = _num(k, v)
        elif k in _BOOLS:
            if not isinstance(v, bool):
                raise InvalidInputError(f"{k} must be true or false.")
            clean[k] = v
        elif k == "cache_mode":
            if v not in src_cache.MODES:
                raise InvalidInputError("cache_mode must be one of: " + ", ".join(src_cache.MODES))
            clean[k] = v
    floor = float(store.DEFAULT_SETTINGS["pace_min_delay"])
    if "pace_min_delay" in clean and clean["pace_min_delay"] < floor:
        raise InvalidInputError(f"pace_min_delay can't be below {floor:g} seconds.")
    cur = store.all_settings()
    merged = {**{k: cur[k] for k in SETTING_KEYS}, **clean}
    # Same normalisation as the tab: a max is never below its min.
    for lo_key, hi_key in (("pace_min_delay", "pace_max_delay"),
                           ("session_break_min_requests", "session_break_max_requests"),
                           ("session_break_min_delay", "session_break_max_delay")):
        if lo_key in clean or hi_key in clean:
            clean[hi_key] = max(merged[lo_key], merged[hi_key])
    for k, v in clean.items():
        store.set_setting(k, v)
    src_http.reset_pacing_state()
    if clean.get("cache_max_mb"):
        # A lowered ceiling applies now, not only after the next import
        # (a raised one finds nothing to remove).
        try:
            src_cache.RawCache().enforce_ceiling()
        except Exception:   # saved either way; the next import trims again
            import applog
            applog.get_logger().warning("Could not trim the source cache", exc_info=True)
    return get_settings()


MAX_PROXY_URL_LEN = 500


def set_proxy_url(url) -> dict:
    """PC-only (the route is local_only): sets or clears ("") the HTTP(S)
    proxy every source request goes through. Never echoed back: settings
    show `proxy_configured` only, and no error names the value. A loopback
    or private address is allowed here (a local proxy is the usual case)."""
    if not isinstance(url, str):
        raise InvalidInputError("The proxy must be text.")
    text = url.strip()
    if text:
        bad = InvalidInputError("Use an http:// or https:// proxy address, e.g. "
                                "http://127.0.0.1:8080.")
        if len(text) > MAX_PROXY_URL_LEN or any(c.isspace() or ord(c) < 32 or ord(c) == 127
                                                for c in text):
            raise bad
        try:
            parts = urlsplit(text)
            host = parts.hostname
            parts.port  # noqa: B018 -- raises ValueError on a bad port
        except ValueError:
            raise bad from None
        if parts.scheme.lower() not in ("http", "https") or not host or parts.query \
                or parts.fragment or parts.path not in ("", "/"):
            raise bad
    store.set_setting("http_proxy_url", text)   # read per request (sources.http)
    return get_settings()


def reset_health(name: str) -> dict:
    _require_source(name)
    health.reset(name)
    return _health_view(name)


def clear_cache(confirm: bool) -> dict:
    if confirm is not True:
        raise InvalidInputError("Clearing the raw-content cache needs confirm=true.")
    src_cache.RawCache().clear_all()
    stats = src_cache.RawCache().stats()
    return {"entries": stats["entries"], "bytes": stats["bytes"]}


def rollback_profile(domain: str, kind: str, version: int) -> list:
    """Makes an earlier saved profile version active again (nothing is
    deleted). ProfileRejected is not a ServiceError, so it is mapped here."""
    if domain not in src_profiles.list_domains():
        raise NotFoundError("No saved profile for that domain.")
    try:
        src_profiles.rollback(domain, kind, version)
    except src_profiles.ProfileRejected:
        raise NotFoundError("No such profile version.") from None
    return next(d for d in list_profiles() if d["domain"] == _scrub(domain))["versions"]


def dismiss_notification(notification_id: int) -> dict:
    if notification_id not in {n["id"] for n in store.list_notifications(include_dismissed=True)}:
        raise NotFoundError("No such notification.")
    store.dismiss_notification(notification_id)
    return next(n for n in list_notifications(include_dismissed=True) if n["id"] == notification_id)


def set_tracked(source: str, series_id: str, tracked: bool, title: str = "", url: str = "",
                drama_id: int = None, principal=None) -> list:
    """Track or untrack one series. Tracking a new series needs this
    process's finished `sources_series_<source>` result for the same
    series (POST /api/sources/{name}/series): its chapters are recorded as
    known, so the first check announces (and auto-imports) nothing old.
    Without one: 409 "Open the series first". The stored URL is that
    result's scheme+host+path URL; the client's `url` is not used."""
    from types import SimpleNamespace

    import background_jobs
    from services import sources_search_service as search
    from services.service_errors import ConflictError

    _require_source(source)
    series_id = (series_id or "").strip()
    if not series_id:
        raise InvalidInputError("series_id is required.")
    exists = any(r["source"] == source and r["series_id"] == series_id
                 for r in store.list_tracked_series())
    if not tracked:
        if not exists:
            raise NotFoundError("That series isn't tracked.")
        store.untrack_series(source, series_id)
        return list_tracked(principal)
    if drama_id is not None and (db.get_drama(drama_id) is None or not
                                 ownership_service.can_edit_drama(principal, drama_id)):
        raise NotFoundError(f"No drama with id {drama_id}.")
    status = background_jobs.get_status(search.SERIES_JOB_PREFIX + source) or {}
    result = status.get("result") if status.get("status") == "done" else None
    if (not isinstance(result, dict) or result.get("series_id") != series_id
            or result.get("error")):
        raise ConflictError("Open the series first, so its current chapters can be "
                            "recorded as already known.", details={"reason": "SERIES_NOT_LOADED"})
    ids = search.known_chapter_ids(result)
    titles = {str(c.get("chapter_id")): c.get("title") or "" for c in result["chapters"]}
    info = result.get("info") or {}
    store.track_series(source, series_id,
                       (title or "").strip() or info.get("title") or series_id,
                       safe_url(info.get("url")), drama_id,
                       known_chapters=[SimpleNamespace(chapter_id=i, title=titles.get(i, ""))
                                       for i in ids])
    return list_tracked(principal)
