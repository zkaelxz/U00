"""
services/bug_report_service.py -- "Report a problem" reports sent from the
React app (feature request, 2026-09-29).

A report is what the viewer typed (what happened / what they expected),
the browser-side context the React capture module gathered (route history,
console errors and warnings, uncaught errors, failed API calls as method +
path + status + code only, versions, user agent, viewport, PC/LAN/remote
mode), an optional PNG/JPEG screenshot, and server-side facts added here
(git commit, a setup-check summary and, when asked, the redacted log tail).

Stored as files, not in the database, under
`<library>/bug_reports/<UTC timestamp>_<n>/`: `report.json`, `report.md`
and `screenshot.png`/`.jpg`. The old db.bug_reports table is unrelated and no longer written.

Every stored string passes `_scrub`: translate_engines.redact_secrets and
diagnostics.redact_for_support (OS user name, absolute paths), plus
session-cookie / CSRF / Cookie-header / URL-userinfo / credential-query
parameter / long-token and user-home-folder redaction. App
routes and API paths are not filesystem paths, so they keep their shape
unless they look like one. Screenshot metadata (EXIF, XMP, PNG text
chunks) is stripped. Nothing returned here contains a filesystem path.

No HTTP types.
"""

import datetime
import json
import os
import re
import subprocess
import threading

import db
import diagnostics
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

DIR_NAME = "bug_reports"
MAX_REPORTS = 100
MAX_TOTAL_BYTES = 250 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
LOG_TAIL_LINES = 40
GIT_TIMEOUT_SECONDS = 5
SETUP_CACHE_SECONDS = 600
COUNTER_FILE = ".last_id"     # high-water mark: an id is never handed out twice

_FOLDER_RE = re.compile(r"^(\d{8}T\d{6}Z)_(\d+)$")
_STAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
_LOCK = threading.Lock()
_CACHE = {}

# Extra redaction on top of redact_for_support. The session cookie and the
# CSRF token by name, any long token-like run (session tokens, keys a
# pattern doesn't know), and user home folders on any OS -- a Windows path
# from another machine carries a user name redact_for_support can't know.
_COOKIE_RE = re.compile(r"(baihe_session\s*[=:]\s*)[^;\s\"']+", re.IGNORECASE)
_CSRF_RE = re.compile(r"(x-csrf-token[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+", re.IGNORECASE)
# Any Cookie / Set-Cookie value (header dump or JSON), to the end of the line.
_COOKIE_HDR_RE = re.compile(r"(?im)((?:set-)?cookie[\"']?\s*[:=]\s*)[^\r\n]*")
# URL userinfo: scheme://user:pass@host -> scheme://***@host.
_USERINFO_RE = re.compile(r"(?<=//)[^/\s@\"'<>]+@")
# Credential-shaped parameters of any length, wherever they sit (a query
# string, a log line, JSON): token=, access_token=, sig=, signature=,
# X-Amz-Signature=, Key-Pair-Id=, auth=, api_key=, password=, ... The name
# set follows sources/ai_extract._SENSITIVE_PARAM (prompt_safe), without its
# need for a preceding ?/&.
_PARAM_RE = re.compile(
    r"(?i)(?<![\w.\-])((?:[\w.\-]*(?:token|sig|auth|session|secret|credential|passw|key|jwt|"
    r"ticket|hmac|policy)[\w.\-]*|x-amz-[\w.\-]+)=)[^&#\s\"'<>\\;,]*")
_TOKENISH_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")
_USER_DIR_RE = re.compile(
    r"(?i)((?:[a-z]:)?[\\/]+(?:users|documents and settings|home)[\\/]+)[^\\/\s\"'<>|:*?]+")
_FS_LIKE_RE = re.compile(
    r"(?i)(\\|^[a-z]:|^/(?:home|users|root|mnt|media|tmp|var|etc|opt|srv|private|volumes)(?:/|$))")


def _reports_dir() -> str:
    return os.path.join(db.LIBRARY_DIR, DIR_NAME)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pre_scrub(text: str) -> str:
    text = _COOKIE_RE.sub(r"\1[REDACTED]", text)
    text = _COOKIE_HDR_RE.sub(r"\1[REDACTED]", text)
    text = _CSRF_RE.sub(r"\1[REDACTED]", text)
    text = _USERINFO_RE.sub("***@", text)
    text = _PARAM_RE.sub(r"\1[REDACTED]", text)
    text = _USER_DIR_RE.sub(r"\1[USER]", text)
    return _TOKENISH_RE.sub("[REDACTED]", text)


def _scrub(value, limit: int = 5000) -> str:
    """Free text: secrets, tokens, user names and absolute paths removed."""
    text = "" if value is None else str(value)[:limit]
    return diagnostics.redact_for_support(_pre_scrub(text))


def _scrub_route(value, limit: int = 300) -> str:
    """An app route ("/drama/3/review") or API path ("/api/jobs/x"): the
    query string is dropped and secrets removed. It only goes through the
    path redaction when it looks like a filesystem path, since that
    redaction would cut every route down to its last segment."""
    text = ("" if value is None else str(value)).split("?", 1)[0].split("#", 1)[-1][:limit]
    text = _pre_scrub(text)
    if _FS_LIKE_RE.search(text):
        return diagnostics.redact_for_support(text)
    import translate_engines
    import getpass
    text = translate_engines.redact_secrets(text)
    user = getpass.getuser()
    if user and len(user) > 2:
        text = re.sub(re.escape(user), "[USER]", text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------------------
# Screenshot: PNG/JPEG only, metadata stripped
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def _strip_png(data: bytes) -> bytes:
    out, pos, n = [_PNG_SIG], len(_PNG_SIG), len(data)
    while pos + 12 <= n:
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > n:
            break
        if kind not in _PNG_DROP:
            out.append(data[pos:end])
        pos = end
        if kind == b"IEND":
            return b"".join(out)
    raise InvalidInputError("The screenshot is not a valid PNG image.")


def _strip_jpeg(data: bytes) -> bytes:
    """Drops APP1..APP15 (EXIF, XMP, ...) and COM segments before the scan."""
    out, pos, n = [data[:2]], 2, len(data)
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker == 0xFF:          # fill byte
            pos += 1
            continue
        if marker == 0xDA:          # start of scan: the rest is image data
            out.append(data[pos:])
            return b"".join(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out.append(data[pos:pos + 2])
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        end = pos + 2 + length
        if length < 2 or end > n:
            break
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
            out.append(data[pos:end])
        pos = end
    raise InvalidInputError("The screenshot is not a valid JPEG image.")


def clean_screenshot(data: bytes):
    """(bytes, extension) for a PNG or JPEG, metadata removed; 422 otherwise."""
    if not data:
        return None, None
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise InvalidInputError("The screenshot is larger than 5 MB.")
    if data.startswith(_PNG_SIG):
        return _strip_png(data), "png"
    if data.startswith(b"\xff\xd8\xff"):
        return _strip_jpeg(data), "jpg"
    raise InvalidInputError("The screenshot must be a PNG or JPEG image.")


# ---------------------------------------------------------------------------
# Server-side facts
# ---------------------------------------------------------------------------

def _cached(name, compute, ttl=None):
    """compute() once per process (or per `ttl` seconds): git and the
    setup checks are not re-run for every report."""
    import time
    now = time.monotonic()
    hit = _CACHE.get(name)
    if hit is not None and (ttl is None or now - hit[0] < ttl):
        return hit[1]
    value = compute()
    _CACHE[name] = (now, value)
    return value


def _git_commit():
    try:
        r = subprocess.run(["git", "rev-parse", "--short=10", "HEAD"], cwd=_project_root(),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = (r.stdout or "").strip()
    return commit if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{4,40}", commit) else None


def _setup_summary() -> str:
    from services import diagnostics_gaps_service
    try:
        s = diagnostics_gaps_service.get_setup_checks()
    except Exception as e:     # a failed check must not lose the report
        return _scrub(f"setup checks failed: {type(e).__name__}")
    parts = [
        f"Python {s['python']['version']} ({'ok' if s['python']['ok'] else 'too old'})",
        "ffmpeg " + ("found" if s["ffmpeg"]["found"] else "MISSING"),
        "JS runtime " + ((s["js_runtime"]["name"] or "found") if s["js_runtime"]["found"]
                         else "MISSING"),
        "CUDA " + ("available" if s["cuda"]["cuda_available"] else
                   "not available" if s["cuda"]["torch_installed"] else "no torch"),
        "files " + ("complete" if s["files"]["all_present"] else "MISSING some"),
        "library " + ("writable" if s["library_writable"] else "NOT writable"),
    ]
    return _scrub(", ".join(parts))


def _log_tail() -> list:
    from services import diagnostics_gaps_service
    try:
        return [_scrub(ln, 1000) for ln in diagnostics_gaps_service.get_log_tail(LOG_TAIL_LINES)]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Normalising what the client sent (the router validated shape and sizes)
# ---------------------------------------------------------------------------

def _clean_client(c: dict) -> dict:
    def entries(key, fields, route_fields=()):
        out = []
        for e in (c.get(key) or [])[:30]:
            row = {}
            for f in fields:
                v = e.get(f)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    row[f] = v
                elif v is not None:
                    row[f] = _scrub_route(v) if f in route_fields else _scrub(v, 1000)
            out.append(row)
        return out

    viewport = c.get("viewport") or {}
    return {
        "what_happened": _scrub(c.get("what_happened")),
        "expected": _scrub(c.get("expected")),
        "route": _scrub_route(c.get("route")),
        "route_history": entries("route_history", ("route", "at"), ("route",))[-10:],
        "console": entries("console", ("level", "message", "at")),
        "errors": entries("errors", ("kind", "message", "source", "at")),
        "failed_requests": entries("failed_requests", ("method", "path", "status", "code", "at"),
                                   ("path",)),
        "app_version": _scrub(c.get("app_version"), 60),
        "api_version": _scrub(c.get("api_version"), 60),
        "environment": _scrub(c.get("environment"), 60),
        "build_id": _scrub(c.get("build_id"), 120),
        "user_agent": _scrub(c.get("user_agent"), 500),
        "viewport": {k: viewport.get(k) for k in ("width", "height", "dpr")
                     if isinstance(viewport.get(k), (int, float))},
        "mode": c.get("mode") if c.get("mode") in ("pc", "lan", "remote", "unknown") else "unknown",
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _fence(text: str) -> str:
    """A code block whose fence is longer than any backtick run inside."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def build_markdown(report: dict, include_server: bool = True) -> str:
    c, s = report["client"], report.get("server") or {}
    vp = c.get("viewport") or {}
    viewport = (f"{vp.get('width', '?')}x{vp.get('height', '?')}"
                + (f" @{vp['dpr']}x" if vp.get("dpr") else "")) if vp else "unknown"
    out = [f"## What happened\n\n{c['what_happened'] or '(not given)'}\n",
           f"## What I expected\n\n{c['expected'] or '(not given)'}\n",
           "## Context\n",
           f"- Report: #{report['id']} (saved {report['created_at']})",
           f"- Page: `{c['route'] or '/'}`",
           f"- Mode: {c['mode']}",
           f"- App: {c['app_version'] or '?'} · API {c['api_version'] or '?'}"
           + (f" ({c['environment']})" if c["environment"] else ""),
           f"- Frontend build: {c['build_id'] or 'unknown'}",
           f"- Browser: {c['user_agent'] or 'unknown'}",
           f"- Viewport: {viewport}",
           "- Screenshot: " + ("saved with the report on the PC (attach it by hand)"
                               if report.get("has_screenshot") else "none"),
           ""]
    if c["route_history"]:
        out.append("## Recent pages\n")
        out += [f"- {_cell(r.get('at'))} `{r.get('route', '')}`" for r in c["route_history"]]
        out.append("")
    if c["failed_requests"]:
        out += ["## Failed API calls\n", "| When | Method | Path | Status | Code |",
                "|---|---|---|---|---|"]
        out += [f"| {_cell(r.get('at'))} | {_cell(r.get('method'))} | `{_cell(r.get('path'))}` "
                f"| {_cell(r.get('status'))} | {_cell(r.get('code'))} |" for r in c["failed_requests"]]
        out.append("")
    if c["errors"]:
        out.append("## Uncaught errors\n")
        out.append(_fence("\n".join(
            f"{e.get('at', '')} [{e.get('kind', '')}] {e.get('message', '')}"
            + (f" ({e['source']})" if e.get("source") else "") for e in c["errors"])))
        out.append("")
    if c["console"]:
        out.append("## Console errors and warnings\n")
        out.append(_fence("\n".join(f"{e.get('at', '')} [{e.get('level', '')}] {e.get('message', '')}"
                                    for e in c["console"])))
        out.append("")
    if include_server and s:
        out += ["## Server (added by the PC)\n",
                f"- Git commit: {s.get('git_commit') or 'unknown'}",
                f"- Setup: {s.get('setup') or 'unknown'}", ""]
        if s.get("log_tail"):
            out.append(f"### Server log (last {len(s['log_tail'])} lines, redacted)\n")
            out.append(_fence("\n".join(s["log_tail"])))
            out.append("")
    elif s:
        out += ["## Server\n", "Server details are saved with the report on the PC.", ""]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _folders() -> dict:
    """{id: folder name} for every saved report."""
    root = _reports_dir()
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return {}
    out = {}
    for name in names:
        m = _FOLDER_RE.match(name)
        if m and os.path.isdir(os.path.join(root, name)):
            out[int(m.group(2))] = name
    return out


def _folder_bytes(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _write(path: str, data, binary=False):
    tmp = path + ".tmp"
    with open(tmp, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as fh:
        fh.write(data)
    os.replace(tmp, path)


def _read_counter(root: str) -> int:
    try:
        with open(os.path.join(root, COUNTER_FILE), encoding="utf-8") as fh:
            value = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0
    return max(value, 0)


def issue_title(what_happened: str) -> str:
    first = (what_happened or "").strip().split("\n", 1)[0]
    short = first if len(first) <= 80 else first[:79] + "\u2026"
    return f"[Bug] {short or 'Problem report'}"


def create_report(client: dict, screenshot: bytes = None, include_server_in_response=True,
                  now: datetime.datetime = None) -> dict:
    """Saves a report and returns {id, stamp, markdown, issue_markdown,
    what_happened, expected, title}. The stored markdown always has the
    server section; `markdown` has it only when include_server_in_response
    (the caller may read diagnostics); `issue_markdown` (for the public
    GitHub link) never has it. The text fields are the scrubbed ones."""
    if not isinstance(client, dict) or not str(client.get("what_happened") or "").strip():
        raise InvalidInputError("Say what happened.")
    shot, ext = clean_screenshot(screenshot)
    cleaned = _clean_client(client)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    with _LOCK:
        # The cap is checked before any of the server-side work below runs.
        existing = _folders()
        root = _reports_dir()
        if len(existing) >= MAX_REPORTS or (
                existing and _folder_bytes(root) + len(shot or b"") > MAX_TOTAL_BYTES):
            raise ConflictError("Too many saved bug reports. Delete some in Diagnostics on the PC.")
        server = {"git_commit": _cached("git", _git_commit),
                  "setup": _cached("setup", _setup_summary, SETUP_CACHE_SECONDS),
                  "log_tail": _log_tail() if client.get("include_server_log", True) else []}
        report_id = max(_read_counter(root), max(existing, default=0)) + 1
        folder = os.path.join(root, f"{stamp}_{report_id}")
        report = {"id": report_id, "created_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                  "has_screenshot": shot is not None, "client": cleaned, "server": server}
        markdown = build_markdown(report, include_server=True)
        os.makedirs(folder, exist_ok=False)
        _write(os.path.join(root, COUNTER_FILE), str(report_id))
        if shot is not None:
            _write(os.path.join(folder, f"screenshot.{ext}"), shot, binary=True)
        _write(os.path.join(folder, "report.md"), markdown)
        _write(os.path.join(folder, "report.json"),
               json.dumps(report, ensure_ascii=False, indent=2))
    public = build_markdown(report, include_server=False)
    return {"id": report_id, "stamp": stamp,
            "markdown": markdown if include_server_in_response else public,
            "issue_markdown": public,
            "what_happened": cleaned["what_happened"], "expected": cleaned["expected"],
            "title": issue_title(cleaned["what_happened"])}


def _check_id(report_id) -> int:
    if not isinstance(report_id, int) or isinstance(report_id, bool) or report_id < 1:
        raise InvalidInputError("A bug report id is a positive whole number.")
    return report_id


def _load(report_id: int):
    name = _folders().get(_check_id(report_id))
    if name is None:
        raise NotFoundError(f"No bug report #{report_id}.")
    folder = os.path.join(_reports_dir(), name)
    try:
        with open(os.path.join(folder, "report.json"), encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        report = None
    return folder, report


def _stamp(folder: str) -> str:
    return _FOLDER_RE.match(os.path.basename(folder)).group(1)


def list_reports() -> list:
    """Newest first: id, folder stamp, when, the first line of "what
    happened", the page, and booleans (screenshot, server log). No paths."""
    out = []
    for report_id in sorted(_folders(), reverse=True):
        folder, r = _load(report_id)
        c = (r or {}).get("client") or {}
        what = (c.get("what_happened") or "").strip().splitlines()
        out.append({
            "id": report_id,
            "stamp": _stamp(folder),
            "created_at": (r or {}).get("created_at"),
            "summary": (what[0][:120] if what else "(unreadable report)"),
            "route": c.get("route") or None,
            "mode": c.get("mode") or None,
            "has_screenshot": bool((r or {}).get("has_screenshot")),
            "has_server_log": bool(((r or {}).get("server") or {}).get("log_tail")),
        })
    return out


def get_report(report_id: int) -> dict:
    folder, _r = _load(report_id)
    try:
        with open(os.path.join(folder, "report.md"), encoding="utf-8") as fh:
            markdown = fh.read()
    except OSError:
        raise NotFoundError(f"Bug report #{report_id} has no readable text.")
    return {"id": report_id, "stamp": _stamp(folder), "markdown": markdown}


def delete_report(report_id: int, stamp: str = None, confirm: bool = False) -> dict:
    """Deletes report `report_id` only if its folder carries `stamp` (from
    the list), so a stale list can never delete a different report."""
    if not isinstance(stamp, str) or not _STAMP_RE.match(stamp):
        raise InvalidInputError("Deleting a bug report needs its stamp from the list.")
    folder, _r = _load(report_id)
    if _stamp(folder) != stamp:
        raise NotFoundError(f"No bug report #{report_id} with that stamp.")
    if confirm is not True:
        raise InvalidInputError("Deleting a bug report needs confirm=true.")
    import shutil
    with _LOCK:
        shutil.rmtree(folder)
    return {"id": report_id, "deleted": True}
