"""
services/notion_service.py -- export a drama's reviewed transcript and its
details to Notion (roadmap item 112), the companion to the Reader's Anki
export. Uses the PC owner's own Notion internal-integration token.

  - get_config / set_config: the target (a Notion database, where each drama
    becomes a row/page, or a parent page, under which each drama becomes a
    sub-page) is stored in app_settings. The token goes to .env
    (BAIHE_NOTION_TOKEN) like the engine keys: set_token / clear_token are
    write-only and callers only ever see `token_configured`.
  - test_connection: who the token belongs to, and whether the target is
    shared with it.
  - start_export: a background job (`notion_export_<drama_id>`) that creates
    the drama's page the first time and updates the same page afterwards.
    Only the page id is stored (dramas.notion_page_id).

Field-scoped updates. On a re-export Baihe changes only what it owns: the
title property, a few optional database properties when the database has
them under these exact names ("Original title", "Lines", "Translated",
"Source language", "Exported"; see OPTIONAL_PROPERTIES), and one toggle
heading block titled "Baihe transcript" that holds the details and the
lines. Anything else on the page, the user's own notes included, is left
alone; anything added inside that block is replaced with it (the block's
first line says so). The new transcript block is inserted right after the old one (the
append's `after`), written completely, and only then is the old one deleted,
so the transcript keeps its place on the page and a failed export never
leaves the page without one. A first export (or one whose old block the user
deleted) appends at the end. A
stored page that is gone, in the trash, or under a different target than the
one set now is not touched: a new page is created instead.

Network: requests go only to the fixed host api.notion.com (every path is
built from validated ids, never from client text). The token is sent only as
`Authorization: Bearer ...` with a `Notion-Version` header; never in a URL,
a log line or an error. Every request has timeout=, follows no redirects and
reads at most MAX_RESPONSE_BYTES. Calls are spaced to Notion's ~3 requests
per second, blocks are appended at most 100 per call (and under the payload
cap), and a 429/409 (and, for reads, deletes and property updates, a
500/502/503/504) is retried after Retry-After or a capped back-off. Writes
that add content are never retried on a 5xx (it may have landed already).
Test connection runs in the request, so it retries once with a short wait.
Errors are fixed text; the only Notion text passed on (a validation message)
is redacted, including the token by value, and shortened.

No Streamlit or FastAPI import: plain dicts in, plain dicts out.
"""
import datetime
import json
import logging
import math
import re
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

import background_jobs
import db
from services import capped_body, drama_service, settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

log = logging.getLogger(__name__)

SETTING = "notion"
TOKEN_ENV = ("BAIHE_NOTION_TOKEN",)
API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
HTTP_TIMEOUT = (5, 20)
MAX_RESPONSE_BYTES = 5_000_000
READ_DEADLINE = 60.0
MIN_INTERVAL = 0.35          # Notion allows an average of 3 requests per second
MAX_RETRIES = 5
MAX_RETRY_WAIT = 30.0
TEST_RETRIES, TEST_RETRY_WAIT = 1, 5.0  # the synchronous Test connection route
MAX_BLOCKS_PER_CALL = 100    # Notion's limit for one append
MAX_PAYLOAD_BYTES = 400_000  # under Notion's 500 KB request cap
MAX_TEXT = 2000              # Notion's limit for one rich-text item (UTF-16 units)
MAX_NOTION_MESSAGE = 120     # Notion's own error text passed on (job panel shows <= 200)
MAX_LINE_TEXT = 10_000
MAX_CHILD_PAGES = 50         # pages of 100 blocks scanned for an old transcript
HEADING_TEXT = "Baihe transcript"
TARGET_TYPES = ("database", "page")
FIELDS = ("en", "zh", "bilingual")
JOB_PREFIX = "notion_export_"
PAGE_URL_BASE = "https://www.notion.so/"

# Database properties filled when the database has one with this exact name
# and a matching type; any other property is never written.
OPTIONAL_PROPERTIES = {
    "Original title": ("text", ("rich_text", "select")),
    "Lines": ("number", ("number",)),
    "Translated": ("number", ("number",)),
    "Source language": ("text", ("rich_text", "select")),
    "Exported": ("date", ("date",)),
}

_TOKEN_RE = re.compile(r"^(secret_|ntn_)[A-Za-z0-9]{20,200}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_ID_IN_TEXT = re.compile(r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
_NOTION_HOSTS = ("notion.so", "www.notion.so")
_RETRY_STATUSES = (409, 429, 500, 502, 503, 504)
# A write (create a page, append blocks) is retried only when Notion says it
# did nothing: a 5xx from its gateway can arrive after the write landed, and
# Notion has no idempotency key, so a retry could add the content twice.
_WRITE_RETRY_STATUSES = (409, 429)

_UNREACHABLE = "Couldn't reach Notion. Check the internet connection and try again."
_BAD_TOKEN = ("Notion refused the token. Copy the internal integration secret from "
              "notion.so/my-integrations again.")
_NOT_SHARED = ("The Notion integration can't see that page or database. Open it in Notion, "
               "choose ••• > Connections and add the integration.")
_BUSY = "Notion is busy (too many requests). Try again in a minute."
_BAD_REPLY = "Notion sent a reply this app could not read."
_NOT_SET_UP = "Set the Notion token and the target page or database in Settings first."

_throttle_lock = threading.Lock()
_last_request = [0.0]
_start_lock = threading.Lock()


class _Missing(Exception):
    """Notion answered 404 (or it is not shared with the integration)."""


# --- settings ----------------------------------------------------------------

def _stored() -> dict:
    saved = db.get_app_setting(SETTING) or {}
    return saved if isinstance(saved, dict) else {}


def _token() -> Optional[str]:
    """Server-side only."""
    return settings_service.resolve_env_names(TOKEN_ENV)


def get_config() -> dict:
    s = _stored()
    return {"target_type": s.get("target_type") if s.get("target_type") in TARGET_TYPES else None,
            "target_id": s.get("target_id") or None,
            "token_configured": bool(_token())}


def _dashed(hex32: str) -> str:
    h = hex32
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def parse_notion_id(value) -> str:
    """A Notion id (32 hex digits, dashes optional) or a notion.so /
    *.notion.site link to the page or database, as the dashed id. A link's
    query (a database view's ?v=...) is ignored: the id is the path's."""
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > 2000:
        raise InvalidInputError("Paste the Notion page or database link (or its id).")
    if "://" in text:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not (host in _NOTION_HOSTS or host.endswith(".notion.site")):
            raise InvalidInputError("That is not a Notion link.")
        text = parts.path.rstrip("/").rsplit("/", 1)[-1]
    match = _ID_IN_TEXT.search(text.lower())
    if not match:
        raise InvalidInputError("Couldn't find a Notion id in that. Copy the page's link "
                                "(Share > Copy link) and paste it here.")
    hex32 = match.group(1).replace("-", "")
    return _dashed(hex32)


def set_config(target_type: Optional[str] = None, target_id: Optional[str] = None) -> dict:
    """Fields left as None keep their saved value; target_id "" clears it."""
    s = _stored()
    if target_type is not None:
        if target_type not in TARGET_TYPES:
            raise InvalidInputError("Choose a database or a page.",
                                    details={"allowed": list(TARGET_TYPES)})
        s["target_type"] = target_type
    if target_id is not None:
        s["target_id"] = parse_notion_id(target_id) if target_id.strip() else None
    db.set_app_setting(SETTING, s)
    return get_config()


def set_token(value, env_path: Optional[str] = None) -> dict:
    """Write-only: stores the token in .env; never echoes it back."""
    token = value.strip() if isinstance(value, str) else ""
    if not _TOKEN_RE.match(token):
        raise InvalidInputError("That does not look like a Notion integration secret "
                                "(it starts with ntn_ or secret_).")
    settings_service.write_env_var(TOKEN_ENV[0], token, env_path)
    return get_config()


def clear_token(env_path: Optional[str] = None) -> dict:
    settings_service.remove_env_vars(TOKEN_ENV, env_path)
    return get_config()


# --- HTTP ----------------------------------------------------------------------

def redact(text, token: Optional[str] = None) -> str:
    """Anything that looks like a key, and the token itself by value."""
    from translate_engines import redact_secrets
    text = str(text or "")
    if token:
        text = text.replace(token, "[REDACTED]")
    text = re.sub(r"\b(secret_|ntn_)[A-Za-z0-9]{10,}", "[REDACTED]", text)
    return redact_secrets(text)


def _sleep(seconds: float):
    time.sleep(seconds)


def _throttle():
    with _throttle_lock:
        wait = _last_request[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            _sleep(wait)
        _last_request[0] = time.monotonic()


def _retry_wait(resp, attempt: int, max_wait: float = MAX_RETRY_WAIT) -> float:
    raw = (resp.headers or {}).get("Retry-After") if resp is not None else None
    try:
        wait = float(raw) if raw is not None else 2.0 ** attempt
    except (TypeError, ValueError):
        wait = 2.0 ** attempt
    if not math.isfinite(wait):
        wait = 2.0 ** attempt
    return min(max(wait, 0.5), max_wait)


def _read_capped(resp) -> bytes:
    """The body, at most MAX_RESPONSE_BYTES and READ_DEADLINE seconds in all."""
    return capped_body.read_capped(resp, MAX_RESPONSE_BYTES, READ_DEADLINE,
                                   lambda: DependencyUnavailableError(_BAD_REPLY),
                                   chunk_size=8 * 1024)


def _parse(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        raise DependencyUnavailableError(_BAD_REPLY) from None
    if not isinstance(data, dict):
        raise DependencyUnavailableError(_BAD_REPLY)
    return data


def _request(method: str, path: str, token: str, body: Optional[dict] = None,
             params: Optional[dict] = None, *, write: bool = False,
             retries: int = MAX_RETRIES, max_wait: float = MAX_RETRY_WAIT,
             job_id: Optional[str] = None) -> dict:
    """One Notion API call. `path` is built by this module from validated
    ids only. Raises _Missing on 404, fixed-text errors otherwise. `write`:
    a call that adds content (see _WRITE_RETRY_STATUSES). `job_id`: a cancel
    request is honoured before each retry wait."""
    retry_on = _WRITE_RETRY_STATUSES if write else _RETRY_STATUSES
    import requests
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
               "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    for attempt in range(retries + 1):
        _throttle()
        session = requests.Session()
        try:
            resp = session.request(method, API_BASE + path, params=params, data=data,
                                   headers=headers, timeout=HTTP_TIMEOUT,
                                   allow_redirects=False, stream=True)
            try:
                status = resp.status_code
                if status in retry_on and attempt < retries:
                    wait = _retry_wait(resp, attempt, max_wait)
                    log.info("Notion answered HTTP %s; retrying in %.1fs", status, wait)
                else:
                    payload = _read_capped(resp)
                    return _handle(status, payload, token)
            finally:
                resp.close()
        except requests.RequestException:
            raise DependencyUnavailableError(_UNREACHABLE) from None
        finally:
            session.close()
        if job_id:
            _check_cancel(job_id)
        _sleep(wait)
    raise DependencyUnavailableError(_BUSY)  # pragma: no cover - loop always returns/raises


def _handle(status: int, payload: bytes, token: str) -> dict:
    if 200 <= status < 300:
        return _parse(payload)
    if status == 401:
        raise DependencyUnavailableError(_BAD_TOKEN)
    if status in (403, 404):
        raise _Missing()
    if status in _RETRY_STATUSES:
        raise DependencyUnavailableError(_BUSY)
    if status == 400:
        try:
            message = json.loads(payload.decode("utf-8")).get("message")
        except (ValueError, UnicodeDecodeError, AttributeError):
            message = None
        if isinstance(message, str) and message.strip():
            text = redact(message, token).strip()[:MAX_NOTION_MESSAGE]
            raise DependencyUnavailableError(f"Notion refused the export: {text}")
    log.info("Notion answered HTTP %s", status)
    raise DependencyUnavailableError(_UNREACHABLE)


def _target_request(method: str, path: str, token: str, **kw) -> dict:
    try:
        return _request(method, path, token, **kw)
    except _Missing:
        raise DependencyUnavailableError(_NOT_SHARED) from None


def _plain(rich) -> str:
    if not isinstance(rich, list):
        return ""
    return "".join(str(r.get("plain_text") or "") for r in rich if isinstance(r, dict))


def _page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return _plain(prop.get("title"))
    return ""


def _configured() -> tuple:
    cfg, token = _stored(), _token()
    target_type, target_id = cfg.get("target_type"), cfg.get("target_id")
    if not token or target_type not in TARGET_TYPES or not target_id:
        raise InvalidInputError(_NOT_SET_UP)
    return token, target_type, target_id


def _target_info(token: str, target_type: str, target_id: str, **kw) -> dict:
    if target_type == "database":
        return _target_request("GET", f"/databases/{target_id}", token, **kw)
    return _target_request("GET", f"/pages/{target_id}", token, **kw)


def test_connection() -> dict:
    token, target_type, target_id = _configured()
    try:
        me = _request("GET", "/users/me", token, retries=TEST_RETRIES, max_wait=TEST_RETRY_WAIT)
    except _Missing:
        raise DependencyUnavailableError(_BAD_TOKEN) from None
    info = _target_info(token, target_type, target_id, retries=TEST_RETRIES,
                        max_wait=TEST_RETRY_WAIT)
    title = _plain(info.get("title")) if target_type == "database" else _page_title(info)
    return {"ok": True, "bot_name": str(me.get("name") or "")[:200],
            "target_title": title[:300], "target_type": target_type}


# --- building the page ------------------------------------------------------------

def _utf16_pieces(text: str) -> list:
    """`text` cut into pieces of at most MAX_TEXT UTF-16 units (how Notion
    counts; an emoji is two), never inside a character."""
    pieces, start, units = [], 0, 0
    for i, ch in enumerate(text):
        width = 2 if ord(ch) > 0xFFFF else 1
        if units + width > MAX_TEXT:
            pieces.append(text[start:i])
            start, units = i, 0
        units += width
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def _rt(text: str, **annotations) -> list:
    """Rich-text items for `text`, split at Notion's 2000-unit limit."""
    items = []
    for piece in _utf16_pieces(text or ""):
        item = {"type": "text", "text": {"content": piece}}
        if annotations:
            item["annotations"] = annotations
        items.append(item)
    return items


def _timestamp(seconds) -> str:
    total = int(max(float(seconds or 0), 0))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _paragraph(rich: list) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich}}


def _line_block(line, field: str):
    en, zh = (line.en or "").strip(), (line.zh or "").strip()
    if field == "en":
        main, extra = en, ""
    elif field == "zh":
        main, extra = zh, ""
    else:
        main, extra = en, zh
    if not main and not extra:
        return None
    rich = _rt(f"{_timestamp(line.start)}  ", color="gray")
    if line.speaker:
        rich += _rt(f"{str(line.speaker)[:200]}: ", bold=True)
    rich += _rt(main[:MAX_LINE_TEXT], italic=True) if line.sfx else _rt(main[:MAX_LINE_TEXT])
    if extra:
        rich += _rt(("\n" if main else "") + extra[:MAX_LINE_TEXT], color="gray")
    return _paragraph(rich)


_FIELD_LABELS = {"en": "English", "zh": "original text", "bilingual": "English and original text"}


REPLACED_NOTE = ("This section is replaced every time the drama is exported from Baihe. "
                 "Add your own notes outside it.")


def _details(drama: dict, total: int, translated: int, field: str, today: str) -> list:
    blocks = [_paragraph(_rt(REPLACED_NOTE, italic=True, color="gray"))]
    rows = [("Original title", drama.get("title_zh")),
            ("Source language", drama.get("source_language")),
            ("Summary", drama.get("episode_summary"))]
    for label, value in rows:
        if value:
            blocks.append(_paragraph(_rt(f"{label}: ", bold=True) + _rt(str(value)[:MAX_LINE_TEXT])))
    blocks.append(_paragraph(_rt(
        f"Exported from Baihe on {today}: {total} lines, {translated} translated "
        f"({_FIELD_LABELS[field]}).", color="gray")))
    blocks.append({"object": "block", "type": "divider", "divider": {}})
    return blocks


def _chunks(blocks: list) -> list:
    """At most MAX_BLOCKS_PER_CALL blocks and MAX_PAYLOAD_BYTES per call."""
    out, current, size = [], [], 0
    for block in blocks:
        n = len(json.dumps(block))
        if current and (len(current) >= MAX_BLOCKS_PER_CALL or size + n > MAX_PAYLOAD_BYTES):
            out.append(current)
            current, size = [], 0
        current.append(block)
        size += n
    if current:
        out.append(current)
    return out


def _title_text(drama: dict, drama_id: int) -> str:
    return (drama.get("title_en") or drama.get("title_zh") or f"Drama {drama_id}").strip()[:500]


def _properties(schema: Optional[dict], drama: dict, drama_id: int, total: int,
                translated: int, today: str) -> dict:
    """Only the properties Baihe owns: the title, and (in a database) the
    OPTIONAL_PROPERTIES the database really has with a matching type."""
    title_rich = _rt(_title_text(drama, drama_id))
    if schema is None:  # a sub-page: its only property is "title"
        return {"title": {"title": title_rich}}
    props = schema.get("properties") or {}
    title_name = next((name for name, p in props.items()
                       if isinstance(p, dict) and p.get("type") == "title"), None)
    if not title_name:
        raise DependencyUnavailableError(_BAD_REPLY)
    out = {title_name: {"title": title_rich}}
    values = {"Original title": drama.get("title_zh") or "", "Lines": total,
              "Translated": translated, "Source language": drama.get("source_language") or "",
              "Exported": today}
    for name, (kind, types) in OPTIONAL_PROPERTIES.items():
        ptype = (props.get(name) or {}).get("type") if isinstance(props.get(name), dict) else None
        if ptype not in types:
            continue
        value = values[name]
        if ptype == "rich_text":
            out[name] = {"rich_text": _rt(str(value)[:MAX_TEXT])}
        elif ptype == "select":  # Notion refuses commas in option names
            name_text = str(value).replace(",", " ").strip()[:100]
            out[name] = {"select": {"name": name_text} if name_text else None}
        elif ptype == "number":
            out[name] = {"number": value}
        elif ptype == "date":
            out[name] = {"date": {"start": value}}
    return out


def _parent_id(page: dict) -> Optional[str]:
    parent = page.get("parent") or {}
    raw = parent.get("database_id") or parent.get("page_id")
    return str(raw).replace("-", "").lower() if raw else None


def _existing_page(token: str, page_id: Optional[str], target_id: str,
                   job_id: Optional[str] = None) -> Optional[str]:
    """The stored page, if it still exists, is not in the trash and still
    sits under the current target. Anything else means "make a new one" (the
    old page is never touched)."""
    hex32 = str(page_id or "").replace("-", "").lower()
    if not _HEX32.match(hex32):
        return None
    page_id = _dashed(hex32)
    try:
        page = _request("GET", f"/pages/{page_id}", token, job_id=job_id)
    except _Missing:
        return None
    if page.get("archived") or page.get("in_trash"):
        return None
    if _parent_id(page) != target_id.replace("-", ""):
        return None
    return page_id


def _is_baihe_heading(block: dict) -> bool:
    if not isinstance(block, dict) or block.get("type") != "heading_2":
        return False
    heading = block.get("heading_2") or {}
    return bool(heading.get("is_toggleable")) and _plain(heading.get("rich_text")) == HEADING_TEXT


def _baihe_headings(token: str, page_id: str, job_id: Optional[str] = None) -> list:
    found, cursor = [], None
    for _ in range(MAX_CHILD_PAGES):
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        data = _target_request("GET", f"/blocks/{page_id}/children", token, params=params,
                               job_id=job_id)
        for block in data.get("results") or []:
            if _is_baihe_heading(block) and isinstance(block.get("id"), str):
                found.append(block["id"])
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not isinstance(cursor, str):
            break
    return found


def _block_id(value) -> str:
    """A block/page id from a Notion reply, checked before it goes into a path."""
    hex32 = str(value or "").replace("-", "").lower()
    if not _HEX32.match(hex32):
        raise DependencyUnavailableError(_BAD_REPLY)
    return _dashed(hex32)


def _check_cancel(job_id: str):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled()


# --- export ---------------------------------------------------------------------

def _export_job(job_id: str, drama_id: int, field: str):
    try:
        _run_export(job_id, drama_id, field)
    except (DependencyUnavailableError, InvalidInputError, NotFoundError) as exc:
        # Fixed text. background_jobs stores it as "RuntimeError: <message>" and
        # the job panel shows at most 200 characters, so messages stay short.
        raise RuntimeError(exc.message) from None


def _run_export(job_id: str, drama_id: int, field: str):
    token, target_type, target_id = _configured()
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError("No drama with that id.")
    lines = db.load_line_objects(drama_id)
    total = len(lines)
    translated = sum(1 for ln in lines if (ln.en or "").strip())
    today = datetime.date.today().isoformat()

    background_jobs.update_progress(job_id, 0.02, "Checking Notion...")
    schema = (_target_info(token, target_type, target_id, job_id=job_id)
              if target_type == "database" else None)
    props = _properties(schema, drama, drama_id, total, translated, today)
    page_id = _existing_page(token, drama.get("notion_page_id"), target_id, job_id)
    _check_cancel(job_id)
    if page_id:
        background_jobs.update_progress(job_id, 0.05, "Updating the Notion page...")
        _target_request("PATCH", f"/pages/{page_id}", token, body={"properties": props},
                        job_id=job_id)
    else:
        background_jobs.update_progress(job_id, 0.05, "Creating the Notion page...")
        parent = {"database_id": target_id} if target_type == "database" else {"page_id": target_id}
        created = _target_request("POST", "/pages", token, write=True, job_id=job_id,
                                  body={"parent": parent, "properties": props})
        page_id = _block_id(created.get("id"))
        # Stored at once: if a later step fails, the next export reuses this page.
        db.set_drama_notion_page_id(drama_id, page_id)

    old = _baihe_headings(token, page_id, job_id)
    blocks = _details(drama, total, translated, field, today)
    blocks += [b for b in (_line_block(ln, field) for ln in lines) if b is not None]
    chunks = _chunks(blocks)
    heading = {"object": "block", "type": "heading_2",
               "heading_2": {"rich_text": _rt(HEADING_TEXT), "is_toggleable": True,
                             "children": chunks[0]}}
    append = {"children": [heading]}
    if old:  # in the old transcript's place, so it stays where the user left it
        append["after"] = _block_id(old[0])
    reply = _target_request("PATCH", f"/blocks/{page_id}/children", token, write=True,
                            job_id=job_id, body=append)
    old_hex = {str(o).replace("-", "").lower() for o in old}
    new_ids = [b.get("id") for b in (reply.get("results") or []) if _is_baihe_heading(b)
               and str(b.get("id") or "").replace("-", "").lower() not in old_hex]
    if len(new_ids) != 1:
        raise DependencyUnavailableError(_BAD_REPLY)
    new_id = _block_id(new_ids[0])
    try:
        for n, chunk in enumerate(chunks[1:], start=1):
            _check_cancel(job_id)
            background_jobs.update_progress(job_id, 0.1 + 0.85 * n / len(chunks),
                                            f"Writing lines ({n * MAX_BLOCKS_PER_CALL} "
                                            f"of about {len(blocks)})...")
            _target_request("PATCH", f"/blocks/{new_id}/children", token, write=True,
                            job_id=job_id, body={"children": chunk})
    except BaseException:
        # Half-written: drop the new block so the old transcript stays the
        # only one. Best effort; a leftover is removed by the next export.
        try:
            _request("DELETE", f"/blocks/{new_id}", token, retries=1, max_wait=2.0)
        except Exception:
            log.info("Could not remove a half-written Notion transcript block")
        raise
    for old_id in old:
        if _block_id(old_id) != new_id:
            try:
                _request("DELETE", f"/blocks/{_block_id(old_id)}", token, job_id=job_id)
            except _Missing:
                pass
    background_jobs.update_progress(job_id, 1.0, "Exported to Notion.")


def start_export(drama_id: int, field: str = "en") -> dict:
    """Starts job `notion_export_<drama_id>`. Checks everything that can be
    checked without the network first. Returns {"job_id"}."""
    drama = db.get_drama(drama_id) if isinstance(drama_id, int) and drama_id > 0 else None
    if drama is None:
        raise NotFoundError("No drama with that id.")
    if field not in FIELDS:
        raise InvalidInputError("Unknown subtitle text.", details={"allowed": list(FIELDS)})
    _configured()
    if not db.load_line_objects(drama_id):
        raise InvalidInputError("This drama has no lines to export yet.")
    job_id = f"{JOB_PREFIX}{drama_id}"
    with _start_lock:
        if drama_service.job_running_for_drama(drama_id):
            raise ConflictError("A background job is still running for this drama. Wait for it "
                                "to finish, then export to Notion.",
                                details={"reason": "job_running"})
        if not background_jobs.start_job(job_id, _export_job, job_id, drama_id, field,
                                         description=f"Notion export (drama #{drama_id})"):
            raise ConflictError("The Notion export is already running for this drama.",
                                details={"reason": "job_running"})
    return {"job_id": job_id}


def page_url(page_id: Optional[str]) -> Optional[str]:
    if not page_id:
        return None
    hex32 = str(page_id).replace("-", "").lower()
    return PAGE_URL_BASE + hex32 if _HEX32.match(hex32) else None


def export_status(drama_id: int) -> dict:
    drama = db.get_drama(drama_id) if isinstance(drama_id, int) and drama_id > 0 else None
    if drama is None:
        raise NotFoundError("No drama with that id.")
    page_id = drama.get("notion_page_id") or None
    url = page_url(page_id)
    return {"drama_id": drama_id, "page_id": page_id if url else None, "page_url": url}
