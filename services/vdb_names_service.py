"""
services/vdb_names_service.py -- an optional personal list of VTuber names
built from the public dd-center/vdb database.

The list is downloaded only when the user presses Download, from one fixed
address, into a single file in the data folder. It never ships with the app
and is never exported, backed up for sharing or sent anywhere: the data is
under the VTuber Public Database International License v1.0 (CC BY-NC-SA 4.0
plus GPL text), which allows personal non-commercial use but requires
attribution and the same licence for anything shared or derived.

A title uses the list only when it opted in (off by default; stored per title
in app_settings like the language-pack choice). Then the names that occur in
the title's text are offered as read-only suggestions in the glossary panel
and added to the translate prompt as plain "Known names" text. A glossary term
always wins over a name here.

Status and every response hold booleans and counts only: never a path or URL.
"""

import contextlib
import json
import logging
import os
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import db
import portable
from lib import http
from services import ownership_service
from services.service_errors import ConflictError, DependencyUnavailableError, NotFoundError
from translate_engines import redact_secrets

logger = logging.getLogger(__name__)

SOURCE_URL = "https://vdb.vtbs.moe/json/list.json"
SOURCE_NAME = "dd-center/vdb"
LICENCE = "VTuber Public Database International License v1.0 (CC BY-NC-SA 4.0 plus GPL text)"
FILE_NAME = "vtuber_names.json"
TEMP_SUFFIX = ".part"   # both names are in .gitignore: a non-installed copy keeps its data folder in the checkout

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT = (10, 30)       # connect, read
FETCH_DEADLINE_SECONDS = 120   # the whole download; the per-read timeout alone restarts on every chunk
MIN_NAME_LEN = 2               # one character matches inside far too many words
MAX_NAME_LEN = 40
MAX_EXTRA_NAMES = 20           # per entry; the real data has a handful
MAX_NAMES = 100_000            # the real list has about 25k
MAX_MATCHES = 200              # names offered for one text
MAX_PROMPT_NAMES = 40
MAX_COUNT_PER_NAME = 1000
FRESH_DAYS = 30
KEPT_TYPES = frozenset({"vtuber", "group"})

_TITLE_KEY = "vdb_names.title.{}"
_lock = threading.Lock()       # one download or delete at a time
_cache_lock = threading.Lock()
_cache: Optional[dict] = None  # {"stamp": (mtime_ns, size), "header": dict, "names": [str]}


def _path() -> str:
    return os.path.join(portable.data_dir(), FILE_NAME)


# --- parsing ------------------------------------------------------------------

def _clean_name(value) -> str:
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if not MIN_NAME_LEN <= len(name) <= MAX_NAME_LEN or "," in name:
        return ""
    # A control or separator character (a newline especially) would break the
    # one-line prompt list.
    if any(unicodedata.category(ch)[0] in "CZ" and ch != " " for ch in name):
        return ""
    return name


def parse_list(data) -> Tuple[int, List[str]]:
    """(kept entry count, sorted unique names) from the decoded upstream JSON.
    A malformed entry is skipped, never fatal; a document with no `vtbs` list
    is refused."""
    vtbs = data.get("vtbs") if isinstance(data, dict) else None
    if not isinstance(vtbs, list):
        raise DependencyUnavailableError("The VTuber database was not in the expected format.")
    names = set()
    kept = 0
    for entry in vtbs:
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str) or entry["type"] not in KEPT_TYPES or entry.get("bot") is True:
            continue
        name = entry.get("name")
        if not isinstance(name, dict):
            continue
        raw = [name.get(k) for k in ("default", "cn", "jp", "en")]
        extra = name.get("extra")
        if isinstance(extra, list):
            raw.extend(extra[:MAX_EXTRA_NAMES])
        found = {n for n in map(_clean_name, raw) if n}
        if not found:
            continue
        kept += 1
        names |= found
        if len(names) > MAX_NAMES:
            raise DependencyUnavailableError("The VTuber database is larger than allowed, so it was refused.")
    return kept, sorted(names)


# --- file ---------------------------------------------------------------------

def _write_atomic(path: str, payload: dict) -> None:
    tmp = path + TEMP_SUFFIX
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _load() -> Optional[dict]:
    """The stored list ({"header", "names"}), or None when there is none or it
    does not read back. Cached until the file changes."""
    global _cache
    path = _path()
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if _cache is not None and _cache["stamp"] == stamp:
            return _cache
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            header, names = data["header"], data["names"]
            if not isinstance(header, dict) or not isinstance(names, list):
                return None
            names = [n for n in map(_clean_name, names) if n]
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return None
        _cache = {"stamp": stamp, "header": header, "names": names}
        return _cache


def _fetched_at(header: dict) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(header.get("fetched_at")))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def status() -> dict:
    stored = _load()
    if stored is None:
        return {"downloaded": False, "entry_count": 0, "name_count": 0,
                "age_days": None, "newer_than_30_days": False}
    fetched = _fetched_at(stored["header"])
    age = max(0, (datetime.now(timezone.utc) - fetched).days) if fetched else None
    entries = stored["header"].get("entry_count")
    return {"downloaded": True, "entry_count": entries if isinstance(entries, int) else 0,
            "name_count": len(stored["names"]), "age_days": age,
            "newer_than_30_days": age is not None and age <= FRESH_DAYS}


def fetch() -> dict:
    """Download the list and replace the stored one. Raises
    DependencyUnavailableError with a fixed message when the download fails;
    the stored list is left as it was."""
    if not _lock.acquire(blocking=False):
        raise ConflictError("The VTuber names are already being downloaded.")
    try:
        try:
            # guard=None: the address is fixed in this module, not supplied by anyone.
            resp = http.get(SOURCE_URL, timeout=FETCH_TIMEOUT, max_bytes=MAX_DOWNLOAD_BYTES,
                            deadline=FETCH_DEADLINE_SECONDS, guard=None,
                            headers={"Accept": "application/json", "User-Agent": "BaiheStudio"})
        except http.FetchError as exc:
            raise DependencyUnavailableError(redact_secrets(exc.message)) from None
        if not resp.ok:
            raise DependencyUnavailableError("The VTuber database could not be downloaded.")
        try:
            data = json.loads(resp.body)
            kept, names = parse_list(data)
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        except (ValueError, TypeError, RecursionError):
            raise DependencyUnavailableError("The VTuber database was not in the expected format.") from None
        if not names:
            raise DependencyUnavailableError("The VTuber database had no usable names.")
        upstream = meta.get("timestamp")
        try:
            _write_atomic(_path(), {
                "header": {"source": SOURCE_NAME, "licence": LICENCE,
                           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "upstream_timestamp": upstream if isinstance(upstream, (int, float, str)) else None,
                           "entry_count": kept},
                "names": names})
        except OSError:
            raise DependencyUnavailableError("The VTuber names could not be saved.") from None
        return status()
    finally:
        _lock.release()


def delete() -> dict:
    with _lock:
        try:
            os.remove(_path())
        except FileNotFoundError:
            pass
        except OSError:
            raise DependencyUnavailableError("The VTuber names could not be deleted.") from None
    return status()


# --- matching -----------------------------------------------------------------

def _is_latin_letter(ch: str) -> bool:
    return ord(ch) < 0x250 and ch.isalpha()


def _count(text: str, name: str) -> int:
    """Case-sensitive occurrences of `name`. An end of the name that is a Latin
    letter must not touch another letter, so "Mel" is not found inside "Melon"."""
    check_start, check_end = _is_latin_letter(name[0]), _is_latin_letter(name[-1])
    count = pos = 0
    while count < MAX_COUNT_PER_NAME:
        pos = text.find(name, pos)
        if pos < 0:
            break
        end = pos + len(name)
        touches = ((check_start and pos > 0 and _is_latin_letter(text[pos - 1]))
                   or (check_end and end < len(text) and _is_latin_letter(text[end])))
        if touches:
            pos += 1
        else:
            count += 1
            pos = end
    return count


def matches_for_text(text: str, limit: int = MAX_MATCHES) -> List[Tuple[str, int]]:
    """(name, occurrences) for each stored name found in `text`, most frequent
    first; empty when nothing is downloaded."""
    stored = _load()
    if stored is None or not text:
        return []
    found = [(n, c) for n in stored["names"] if n in text and (c := _count(text, n))]
    found.sort(key=lambda m: (-m[1], m[0]))
    return found[:limit]


def entries_for_text(text: str, limit: int = MAX_MATCHES) -> List[str]:
    """The stored names that actually occur in `text`."""
    return [n for n, _ in matches_for_text(text, limit)]


# --- per title ----------------------------------------------------------------


def is_enabled(drama_id) -> bool:
    stored = db.get_app_setting(_TITLE_KEY.format(drama_id))
    return isinstance(stored, dict) and stored.get("enabled") is True


def get_title(drama_id: int, principal=None) -> dict:
    ownership_service.require_visible(principal, "drama", drama_id)
    ownership_service.require_drama(drama_id)
    return {"enabled": is_enabled(drama_id), "downloaded": status()["downloaded"]}


def set_title(drama_id: int, enabled: bool, principal=None) -> dict:
    ownership_service.require_editable(principal, "drama", drama_id)
    ownership_service.require_drama(drama_id)
    db.set_app_setting(_TITLE_KEY.format(drama_id), {"enabled": bool(enabled)})
    return get_title(drama_id, principal)


def forget_title(drama_id: int) -> None:
    """Drops the title's choice so a deleted title leaves no row behind. Best
    effort, like language_pack_service.forget_title: the title is already gone."""
    try:
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (_TITLE_KEY.format(drama_id),))
            conn.commit()
    except sqlite3.Error as e:
        logger.warning("Could not drop title %s's VTuber-names choice: %s", drama_id, type(e).__name__)


_PROMPT_HEADING = "\n\nKnown names (keep as written): "
_PROMPT_BLOCK_RE = re.compile(re.escape(_PROMPT_HEADING) + r"[^\n]*")


def strip_from_snapshot(conn) -> None:
    """Takes this list's prompt block out of the bulk jobs' saved prompt
    arguments in a database snapshot (a backup), so the names never leave
    the PC in one."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "bulk_jobs" not in have:
        return
    rows = conn.execute("SELECT id, translate_args FROM bulk_jobs "
                        "WHERE translate_args LIKE '%Known names (keep as written)%'").fetchall()
    for job_id, raw in rows:
        try:
            args = json.loads(raw)
        except ValueError:
            args = None
        if isinstance(args, dict) and isinstance(args.get("style_guidelines"), str):
            args["style_guidelines"] = _PROMPT_BLOCK_RE.sub("", args["style_guidelines"])
            cleaned = json.dumps(args)
        else:
            cleaned = None      # not ours to parse: drop it rather than ship the names
        conn.execute("UPDATE bulk_jobs SET translate_args = ? WHERE id = ?", (cleaned, job_id))


def block_for(drama: dict, lines, glossary_terms=None) -> str:
    """The prompt section for a run ("" or a blank line then the block, ready
    to append to the guidelines). Chosen from every line the run translates so
    all of its batches share one identical prompt. Off unless the title opted
    in; a name a glossary term already covers is left out."""
    if not drama or drama.get("id") is None or not is_enabled(drama["id"]):
        return ""
    text = "\n".join(getattr(ln, "zh", "") or "" for ln in lines)
    covered = [s for t in (glossary_terms or [])
               for s in (t.get("term_original"), *(t.get("aliases") or [])) if s]
    names = [n for n in entries_for_text(text) if not any(n in s for s in covered)]
    if not names:
        return ""
    return _PROMPT_HEADING + ", ".join(names[:MAX_PROMPT_NAMES])
