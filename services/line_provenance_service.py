"""
services/line_provenance_service.py -- Step 41 item 4: what produced each
line's current translation (engine, model, prompt version, glossary
version, a hash of the run's settings and of the source text, and the
Baihe version). UI-free; rows live in db.py's `line_provenance`, one per
line (the latest translation wins), keyed by the permanent line id.

`tracker(drama_id, lines, ...)` wraps a translate run's save callback: it
remembers each line's `en` when the run starts and, whenever a batch is
saved, records provenance for the lines whose `en` that run changed. It
never touches the `lines` table itself.

No key, token or path is stored; `settings` should be the run's plain
options (locale, style preset, context window...).
"""

import contextlib
import hashlib
import os
import re
import subprocess
import threading
import time

import db
from services.job_checkpoint_service import hash_text, settings_hash

GIT_TIMEOUT_SECONDS = 3
_version_lock = threading.Lock()
_version = None


@contextlib.contextmanager
def _conn():
    with contextlib.closing(db.get_conn()) as conn:
        with conn:
            yield conn


def software_version() -> str:
    """The Baihe git commit (short), read once per process; "unknown"
    when this isn't a git checkout."""
    global _version
    with _version_lock:
        if _version is None:
            _version = "unknown"
            try:
                r = subprocess.run(["git", "rev-parse", "--short=10", "HEAD"],
                                   cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
                commit = (r.stdout or "").strip()
                if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{4,40}", commit):
                    _version = commit
            except (OSError, subprocess.SubprocessError):
                pass
        return _version


def glossary_hash(glossary_terms) -> str:
    """Order-independent hash of the glossary a run used (the fields that
    change a translation), shortened; "" for no glossary."""
    rows = sorted(
        "\x1f".join(str((t or {}).get(k) or "") for k in
                    ("term_original", "term_translation", "policy", "enforce_exact"))
        for t in (glossary_terms or []))
    if not rows:
        return ""
    return hashlib.sha256("\x1e".join(rows).encode("utf-8")).hexdigest()[:16]


def record(drama_id, line_ids_to_zh: dict, engine, model, prompt_version, glossary_hash_value,
           settings=None, now=None):
    """line_ids_to_zh: {permanent line id: source text}."""
    if not line_ids_to_zh:
        return
    now = time.time() if now is None else now
    s_hash = settings_hash(settings)[:16]
    version = software_version()
    rows = [(drama_id, int(line_id), engine, model, prompt_version, glossary_hash_value, s_hash,
             hash_text(zh)[:16], version, now)
            for line_id, zh in line_ids_to_zh.items() if line_id is not None]
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO line_provenance (drama_id, line_id, engine, model, "
            "prompt_version, glossary_hash, settings_hash, input_hash, software_version, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def get(drama_id, line_id):
    with _conn() as conn:
        conn.row_factory = None
        row = conn.execute(
            "SELECT engine, model, prompt_version, glossary_hash, settings_hash, input_hash, "
            "software_version, created_at FROM line_provenance WHERE drama_id = ? AND line_id = ?",
            (drama_id, line_id)).fetchone()
    if row is None:
        return None
    keys = ("engine", "model", "prompt_version", "glossary_hash", "settings_hash",
            "input_hash", "software_version", "created_at")
    return dict(zip(keys, tuple(row)))


def describe(prov) -> str:
    """One plain sentence for the "What happened here?" view."""
    if not prov:
        return ("No per-line record for this translation: it was made before Baihe "
                "started recording them, or by an edit rather than a translate run.")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(prov["created_at"]))
    glossary = (f"glossary version {prov['glossary_hash']}" if prov.get("glossary_hash")
                else "no glossary")
    return (f"Translated {when} by {prov.get('engine') or 'an unknown engine'}"
            f"{' (' + prov['model'] + ')' if prov.get('model') else ''}, prompt version "
            f"{prov.get('prompt_version') or '?'}, {glossary}, Baihe {prov.get('software_version')}.")


def tracker(drama_id, lines, engine_name, model, prompt_version, glossary_terms, settings=None):
    """Returns `on_save(lines)`: call it with the lines a translate run just
    saved; it records provenance for lines whose `en` changed since the
    run started. Never raises."""
    before = {ln.id: (ln.en or "") for ln in lines if getattr(ln, "id", None) is not None}
    g_hash = glossary_hash(glossary_terms)
    lock = threading.Lock()

    def on_save(saved):
        try:
            changed = {}
            with lock:
                for ln in saved:
                    line_id = getattr(ln, "id", None)
                    en = ln.en or ""
                    if line_id is None or not en.strip() or before.get(line_id) == en:
                        continue
                    before[line_id] = en
                    changed[line_id] = ln.zh
            record(drama_id, changed, engine_name, model, prompt_version, g_hash, settings)
        except Exception:
            pass
    return on_save
