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
from services.service_errors import NotFoundError
from sources import auth_browser, cache as src_cache, health, ladder, registry, store
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
    "cache_mode", "check_interval_hours", "auto_queue_new_chapters",
    "demo_source_enabled", "extraction_diagnostics",
)

# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------

_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
_WIN_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>]*")
_UNC_PATH = re.compile(r"\\\\[^\s\"'<>]+")
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


def list_tracked() -> list:
    return [{"source": r["source"], "series_id": r["series_id"], "title": _scrub(r["title"]),
             "url": safe_url(r.get("url")), "drama_id": r.get("drama_id"),
             "last_checked": r.get("last_checked"),
             "last_check_error": _scrub(r.get("last_check_error"))}
            for r in store.list_tracked_series()]


def list_notifications(include_dismissed: bool = False) -> list:
    return [{"id": r["id"], "source": r["source"], "series_id": r["series_id"],
             "chapter_id": r["chapter_id"], "title": _scrub(r.get("title")),
             "created_at": r["created_at"], "dismissed": bool(r["dismissed"])}
            for r in store.list_notifications(include_dismissed=include_dismissed)]
