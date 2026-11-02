"""voice_venv.py -- voice engines that live in a venv of their own, and running a batch of lines through one.

Some voice engines pin packages the app can't share (qwen-tts needs an older transformers
than Baihe's own models), so each gets its own interpreter under
`<data dir>/voice_engines/<engine>/`. The app never imports such an engine: it writes a
spec file and runs voice_worker.py with the venv's Python, through lib.proc.stream_tree,
so a cancel or timeout kills the whole child tree.

The spec holds the text, the reference clip and the output paths, so none of them travel
on the command line. Event lines carry a random per-run nonce that only the spec file
knows: an engine's own error output shares the stream, and a fixed prefix could be forged
by text it echoes. Audio goes back as WAV files at the paths the parent chose, never
over the pipe.
"""

import json
import os
import re
import secrets
import tempfile

import portable
import voice_worker
from lib import proc

VENVS_DIRNAME = "voice_engines"
# Written last by the installer, so a venv whose install was interrupted never counts
# as ready.
READY_MARKER = ".baihe-ready"
WORKER_SCRIPT = os.path.abspath(voice_worker.__file__)
_ENGINE_NAME = re.compile(r"[a-z0-9_]{1,40}")
_EVENT_KEYS = frozenset({"loaded", "done", "failed", "error", "fatal"})


def venv_dir(engine: str) -> str:
    # The engine name becomes a folder name; only the app's fixed engine keys may reach here.
    if not _ENGINE_NAME.fullmatch(engine or ""):
        raise ValueError("bad voice engine name")
    return os.path.join(portable.data_dir(), VENVS_DIRNAME, engine)


def venv_python(engine: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir(engine), "Scripts", "python.exe")
    return os.path.join(venv_dir(engine), "bin", "python")


def is_ready(engine: str) -> bool:
    """Whether the engine's venv finished installing: its Python and the ready marker exist."""
    return (os.path.isfile(venv_python(engine))
            and os.path.isfile(os.path.join(venv_dir(engine), READY_MARKER)))


def _text(value) -> str | None:
    return value[:voice_worker.ERROR_MAX_CHARS] if isinstance(value, str) else None


def _check_event(event, pending: set):
    """The event with its id marked as answered, or None for a malformed one or an id
    that wasn't asked for (or was already answered)."""
    if not isinstance(event, dict) or not event or not set(event) <= _EVENT_KEYS:
        return None
    if set(event) == {"loaded"}:
        return event if event["loaded"] is True else None
    if set(event) == {"fatal"}:
        detail = _text(event["fatal"])
        return None if detail is None else {"fatal": ENGINE_FAILED, "detail": detail}
    if set(event) == {"done"} and isinstance(event["done"], str) and event["done"] in pending:
        pending.discard(event["done"])
        return event
    if (set(event) == {"failed", "error"} and isinstance(event["failed"], str)
            and event["failed"] in pending):
        detail = _text(event["error"])
        if detail is None:
            return None
        pending.discard(event["failed"])
        return {"failed": event["failed"], "error": LINE_FAILED, "detail": detail}
    return None


# The engine's own error text can name local paths or echo the text being spoken, so
# callers get a fixed sentence to show and the engine's text only as "detail", for logs.
ENGINE_FAILED = "The voice engine could not start. Check it in Diagnostics."
LINE_FAILED = "The voice engine could not speak this line."
NO_AUDIO = "The voice engine wrote no audio."


def run_batch(engine: str, items: list, workdir: str, timeout: float, cancel=None,
              options: dict | None = None, on_start=None):
    """Speaks `items` ({"id": str, "out": wav path, plus the engine's own fields such as
    text, language, ref_audio, ref_text}) in one child. Yields one event per checked line
    ({"loaded": True}, {"done": id}, {"failed": id, "error", "detail"},
    {"fatal", "detail"}), then {"returncode", "timed_out", "cancelled", "dropped",
    "unanswered": [ids]}. "error" and "fatal" are fixed sentences; "detail" is the
    engine's own text, which may name local paths: log it, never show or store it.
    Any file already at an "out" path is removed first, so a "done" only counts when the
    engine wrote that file in this run. Stopping the iteration early kills the child tree."""
    ids = [item["id"] for item in items]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) for i in ids):
        raise ValueError("voice batch item ids must be unique strings")
    python = venv_python(engine)
    if not os.path.isfile(python):
        # Popen's own error would name the venv's full path.
        raise RuntimeError(ENGINE_FAILED)
    out_by_id = {item["id"]: item["out"] for item in items}
    for out in out_by_id.values():
        if os.path.isfile(out):
            os.remove(out)
    pending = set(ids)
    nonce = secrets.token_hex(16)
    prefix = f"{voice_worker.PREFIX}{nonce} "
    # A name of its own per batch, so two batches sharing a workdir never read or delete
    # each other's spec.
    fd, spec_path = tempfile.mkstemp(prefix="voice_spec_", suffix=".json", dir=workdir)
    dropped = 0
    stream = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"nonce": nonce, "engine": engine, "options": options or {},
                       "items": items}, f, ensure_ascii=False)
        # -I keeps the app's folder and PYTHON* variables out of the venv's import path,
        # so the worker can't pick up the app's own packages; it also ignores
        # PYTHONIOENCODING, hence -X utf8 for non-ASCII text and paths on Windows.
        cmd = [python, "-I", "-X", "utf8", WORKER_SCRIPT, spec_path]
        stream = proc.stream_tree(cmd, timeout, cwd=workdir, cancel=cancel, on_start=on_start)
        for item in stream:
            line = item.get("line")
            if line is None:
                yield dict(item, dropped=dropped, unanswered=[i for i in ids if i in pending])
                continue
            if not line.startswith(prefix):
                continue
            try:
                event = _check_event(json.loads(line[len(prefix):]), pending)
            except ValueError:
                event = None
            if event is None:
                dropped += 1
                continue
            if "done" in event:
                out = out_by_id[event["done"]]
                if not (os.path.isfile(out) and os.path.getsize(out) > 0):
                    event = {"failed": event["done"], "error": NO_AUDIO, "detail": ""}
            yield event
    finally:
        if stream is not None:
            stream.close()
        # The spec holds the text being spoken; it goes on a cancel or kill too.
        try:
            os.remove(spec_path)
        except OSError:
            pass
