"""
services/maintenance_assistant_service.py -- the in-app AI maintenance
assistant, read-only v1 (roadmap Step 42). UI-free; the API
(api/routers/assistant_routes.py, every route PC-only) and the React
Assistant page call it.

What it does: answers "why did X fail" / "where is Y" / "what changed"
questions about Baihe itself by letting a configured LLM engine call a
fixed set of READ-ONLY tools (list/read/search the code, read-only git,
the redacted log, job history, the support report, dependency and model
checks, one test file at a time), then returns the answer, any proposed
fix as a patch the user applies by hand, and backlog items it suggests.

Read-only is structural, not a prompt instruction: READ_ONLY_TOOLS below
is the only tool table, every entry is 🟢 in action_tiers, and no tool
that writes a file, runs a mutating git command, changes a setting or
touches the library exists in this module. A tool name the model invents
("write_file", "apply_patch") is answered "no such tool" and nothing
runs. The backlog (roadmap item 7) is written only by the user through
its own routes; the assistant can only *suggest* items.

Reused from the Streamlit-era App Assistant (Step 18b, app_help.py):
qa._dispatch_chat for the multi-engine Claude/OpenAI-shaped/Gemini/Ollama
chat call, and its "answer from what you can see, say so honestly
otherwise" discipline. Not reused: app_help's grounding, which parses
tabs/*_tab.py (Streamlit, being deleted); the assistant searches the
real code (frontend/src included) with search_code instead.

Safety rails on every tool: paths are repo-relative, resolved and kept
inside the repo, and never reach the library folder, .git internals,
virtualenvs, node_modules, dotfiles or anything named like a secret; all
text leaving a tool has keys/tokens and this PC's folder prefixes
removed (_redact); in a git checkout only git-tracked files are
readable (so an untracked local config never reaches the engine);
subprocesses (git, pytest) take a fixed argument list, a timeout, and no
shell; a test run gets a throwaway library and no keys.
"""

import contextlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time

import action_tiers
import db
import diagnostics
from services import diagnostics_gaps_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError, ServiceError)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_QUESTION_CHARS = 4000
MAX_CHAT_TURNS = 20
MAX_CHAT_TURN_CHARS = 8000
MAX_CHAT_TOTAL_CHARS = 60000
MAX_ROUNDS = 6              # model turns per question (tool rounds + the final answer)
MAX_TOOL_CALLS_PER_ROUND = 4
MAX_TOOL_OUTPUT_CHARS = 12000
MAX_READ_LINES = 400
MAX_LIST_ENTRIES = 300
MAX_SEARCH_HITS = 80
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_GIT_OUTPUT_CHARS = 20000
GIT_TIMEOUT_SECONDS = 20
TEST_TIMEOUT_SECONDS = 300
MAX_BACKLOG_TEXT = 1000
MAX_BACKLOG_ITEMS = 500
MAX_MODEL_CHARS = 100
MAX_OUTPUT_TOKENS = 4000
BACKLOG_KINDS = ("bug", "feature", "note")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^-]{0,99}$")

_SETTINGS_PREFIX = "assistant."

# Text files the assistant may read or search.
_TEXT_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".html", ".md",
    ".txt", ".toml", ".ini", ".cfg", ".json", ".yml", ".yaml", ".bat", ".ps1",
    ".sh", ".spec",
})
# Directory names never listed, read or searched, wherever they appear.
_DENIED_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".pytest_cache", "venv", ".venv", "env",
    "dist", "build", "library", "test-results", "playwright-report", ".mypy_cache",
    ".ruff_cache",
})
# Dot-directories that are ordinary project content.
_ALLOWED_DOT_DIRS = frozenset({".claude", ".github", ".streamlit"})
# On top of translate_engines.redact_secrets: GitHub tokens and PEM keys.
_EXTRA_SECRET_PATTERNS = [
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
               re.DOTALL),
]
_SECRET_NAME_RE = re.compile(r"(^\.env|secret|token|password|credential|cookie|\.key$|\.pem$|"
                             r"\.p12$|\.pfx$|\.log$|\.db$|\.sqlite)", re.IGNORECASE)


def _redact(text) -> str:
    """Keys and tokens out, plus this PC's absolute project/home folder
    prefixes. Deliberately NOT diagnostics.redact_for_support: that one
    collapses every "a/b/c" to ".../c" and replaces the OS user name as a
    bare substring, which mangles repo-relative paths, code and patches.
    The log, job history and support report tools are already redacted
    with it by the functions they call."""
    import translate_engines
    text = translate_engines.redact_secrets("" if text is None else str(text)) or ""
    for pattern in _EXTRA_SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    for prefix in _private_prefixes():
        text = text.replace(prefix, "")
    return text


def _private_prefixes() -> list:
    out = []
    root = os.path.realpath(repo_root())
    for base in (root, os.path.abspath(repo_root())):
        out += [base + os.sep, base + "/"]
    home = os.path.expanduser("~")
    if home and home not in ("/", "~") and len(home) > 3:
        out += [home + os.sep, home + "/"]
    # Longest first so the repo prefix wins over the home folder it sits in.
    return sorted(set(out), key=len, reverse=True)


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... cut: {len(text) - limit} more characters]"


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Settings (Developer Mode, default engine)
# ---------------------------------------------------------------------------

def _llm_engine_choices() -> list:
    import translate_engines
    return sorted(n for n in translate_engines.ENGINES
                  if n not in translate_engines.TRANSLATION_ONLY_ENGINES)


def _get(key: str, default=None):
    try:
        return db.get_app_setting(_SETTINGS_PREFIX + key, default)
    except Exception:
        return default


# Engines that run on this PC: code and logs never leave it.
LOCAL_ENGINES = frozenset({"ollama", "test_offline"})
DEFAULT_ENGINE = "ollama"


def _cloud_consent() -> dict:
    raw = _get("cloud_consent", {})
    return {k: True for k, v in raw.items() if v is True} if isinstance(raw, dict) else {}


def cloud_consent_given(engine_name: str) -> bool:
    """A local engine needs no consent; a cloud one needs the owner's
    saved, per-provider "allow sending code and logs" consent."""
    return engine_name in LOCAL_ENGINES or _cloud_consent().get(engine_name) is True


def require_cloud_consent(engine_name: str):
    if not cloud_consent_given(engine_name):
        raise ConflictError(
            f"Sending this app's code and logs to {engine_name} isn't allowed yet. Allow it "
            "for that engine in the assistant's settings, or use Ollama to keep everything "
            "on this PC.", details={"reason": "cloud_consent_required", "engine": engine_name})


def developer_mode_enabled() -> bool:
    """Off by default; a DB hiccup reads as off."""
    return _get("developer_mode", False) is True


def get_settings() -> dict:
    engine = _get("engine")
    model = _get("model")
    review_engine = _get("review_engine")
    review_model = _get("review_model")
    choices = _llm_engine_choices()
    return {
        "developer_mode": developer_mode_enabled(),
        "engine": engine if engine in choices else None,
        "model": model if isinstance(model, str) and model else None,
        "engine_choices": choices,
        # Step 60: implement -> independent review, off by default.
        "roles_enabled": _get("roles_enabled", False) is True,
        "review_engine": review_engine if review_engine in choices else None,
        "review_model": review_model if isinstance(review_model, str) and review_model else None,
        "default_engine": DEFAULT_ENGINE,
        "local_engines": sorted(LOCAL_ENGINES & set(choices)),
        # Per cloud engine: may the assistant send code and logs to it?
        "cloud_consent": {c: cloud_consent_given(c) for c in choices if c not in LOCAL_ENGINES},
    }


def _check_engine_name(name, field="engine"):
    if name is None:
        return None
    if not isinstance(name, str) or name not in _llm_engine_choices():
        raise InvalidInputError(f"{field} must be one of the chat-capable engines.")
    return name


def _check_model(model, field="model"):
    if model is None or model == "":
        return None
    if (not isinstance(model, str) or len(model) > MAX_MODEL_CHARS
            or re.search(r"[\s\x00-\x1f\x7f]", model)):
        raise InvalidInputError(
            f"{field} must be at most {MAX_MODEL_CHARS} characters with no spaces or control characters.")
    return model


def set_settings(updates: dict) -> dict:
    """Validates everything before writing anything."""
    if not isinstance(updates, dict):
        raise InvalidInputError("Settings must be an object.")
    cleaned = {}
    for key, value in updates.items():
        if key in ("developer_mode", "roles_enabled"):
            if not isinstance(value, bool):
                raise InvalidInputError(f"{key} must be true or false.")
            cleaned[key] = value
        elif key in ("engine", "review_engine"):
            cleaned[key] = _check_engine_name(value, key)
        elif key in ("model", "review_model"):
            cleaned[key] = _check_model(value, key)
        elif key == "cloud_consent":
            if not isinstance(value, dict) or len(value) > 20:
                raise InvalidInputError("cloud_consent must map engine names to true/false.")
            merged = _cloud_consent()
            for eng, allowed in value.items():
                _check_engine_name(eng, "cloud_consent engine")
                if eng in LOCAL_ENGINES or not isinstance(allowed, bool):
                    raise InvalidInputError("cloud_consent is for cloud engines, as true/false.")
                merged[eng] = allowed
            cleaned[key] = {k: v for k, v in merged.items() if v is True}
        else:
            raise InvalidInputError("Unknown assistant setting.")
    current = get_settings()
    for eng, mod in (("engine", "model"), ("review_engine", "review_model")):
        if eng in cleaned and mod not in cleaned and cleaned[eng] != current[eng]:
            cleaned[mod] = None  # a model name belongs to the engine it was saved with
    for key, value in cleaned.items():
        db.set_app_setting(_SETTINGS_PREFIX + key, value)
    return get_settings()


def _require_developer_mode():
    if not developer_mode_enabled():
        raise ConflictError("Developer Mode is off. Turn it on in Settings to use the assistant.")


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _is_denied_part(part: str) -> bool:
    if part in _DENIED_DIRS:
        return True
    if part.startswith(".") and part not in _ALLOWED_DOT_DIRS:
        return True
    return False


_OUTSIDE = "That path is outside what the assistant may read."


def _denied_rel(parts: list, want_dir: bool) -> bool:
    if any(_is_denied_part(p) for p in parts):
        return True
    return bool(parts and not want_dir and _SECRET_NAME_RE.search(parts[-1]))


def _tracked_files():
    """The set of git-tracked repo-relative paths, or None when this
    isn't a git checkout (a packaged install: the name rules still
    apply). Untracked files (a local settings.local.json, a stray
    credentials file) are never readable in a checkout."""
    try:
        out = subprocess.run(["git", "-c", "core.fsmonitor=", "ls-files", "-z"], cwd=repo_root(),
                             capture_output=True, timeout=GIT_TIMEOUT_SECONDS,
                             stdin=subprocess.DEVNULL, env=_git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return {p for p in out.stdout.decode("utf-8", "replace").split("\0") if p}


def _is_tracked(rel: str, tracked=...) -> bool:
    tracked = _tracked_files() if tracked is ... else tracked
    return tracked is None or rel in tracked


def _resolve(path, *, want_dir: bool) -> str:
    """Repo-relative path -> absolute path inside the repo, or an
    InvalidInputError. Never echoes the rejected path back."""
    root = os.path.realpath(repo_root())
    if path is None:
        path = ""
    if not isinstance(path, str) or len(path) > 300 or "\x00" in path:
        raise InvalidInputError("path must be a short repo-relative path.")
    rel = path.replace("\\", "/").strip()
    if rel in ("", ".", "./"):
        rel = ""
    if rel.startswith(("/", ":")) or re.match(r"^[A-Za-z]:", rel):
        raise InvalidInputError(_OUTSIDE)
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or _denied_rel(parts, want_dir):
        raise InvalidInputError(_OUTSIDE)
    full = os.path.realpath(os.path.join(root, *parts))
    if full != root and not full.startswith(root + os.sep):
        raise InvalidInputError(_OUTSIDE)
    library = os.path.realpath(db.LIBRARY_DIR)
    if full == library or full.startswith(library + os.sep):
        raise InvalidInputError(_OUTSIDE)
    # Same rules on the resolved path (a symlink/junction or an 8.3 short
    # name must not get around them).
    real_parts = [p for p in _rel(full).split("/") if p not in ("", ".")]
    if _denied_rel(real_parts, want_dir):
        raise InvalidInputError(_OUTSIDE)
    if not want_dir and not _is_tracked(_rel(full)):
        raise InvalidInputError(_OUTSIDE)
    if want_dir and not os.path.isdir(full):
        raise NotFoundError("No such folder in the project.")
    if not want_dir:
        if not os.path.isfile(full):
            raise NotFoundError("No such file in the project.")
        if os.path.splitext(full)[1].lower() not in _TEXT_EXTENSIONS:
            raise InvalidInputError("Only source and text files can be read.")
    return full


def _rel(full: str) -> str:
    return os.path.relpath(full, os.path.realpath(repo_root())).replace(os.sep, "/")


def _walk_text_files(start: str):
    tracked = _tracked_files()
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if not _is_denied_part(d)
                             and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            if _SECRET_NAME_RE.search(name) or name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in _TEXT_EXTENSIONS:
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not _is_tracked(_rel(full), tracked):
                continue
            yield full


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------

def _int_arg(args, name, default, low, high):
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidInputError(f"{name} must be a number.")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise InvalidInputError(f"{name} must be a number.") from None
    return max(low, min(high, value))


def _str_arg(args, name, default="", max_len=200):
    value = args.get(name, default)
    if value is None:
        value = default
    if not isinstance(value, str) or len(value) > max_len:
        raise InvalidInputError(f"{name} must be text of at most {max_len} characters.")
    return value


def tool_list_files(args: dict) -> str:
    full = _resolve(_str_arg(args, "path", "", 300), want_dir=True)
    tracked = _tracked_files()
    tracked_dirs = None
    if tracked is not None:
        tracked_dirs = {"/".join(p.split("/")[:i]) for p in tracked for i in range(1, p.count("/") + 1)}
    entries = []
    for name in sorted(os.listdir(full)):
        rel = _rel(os.path.join(full, name))
        if tracked is not None and rel not in tracked and rel not in tracked_dirs:
            continue
        child = os.path.join(full, name)
        if os.path.islink(child):
            continue
        if os.path.isdir(child):
            if not _is_denied_part(name):
                entries.append(name + "/")
        elif not name.startswith(".") and not _SECRET_NAME_RE.search(name):
            entries.append(name)
        if len(entries) >= MAX_LIST_ENTRIES:
            entries.append("[... more entries not shown]")
            break
    return f"{_rel(full) or '.'}:\n" + "\n".join(entries)


def tool_read_file(args: dict) -> str:
    full = _resolve(_str_arg(args, "path", "", 300), want_dir=False)
    start = _int_arg(args, "start", 1, 1, 10_000_000)
    end = _int_arg(args, "end", start + 199, start, start + MAX_READ_LINES - 1)
    out = []
    total = 0
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        for number, line in enumerate(f, 1):
            total = number
            if start <= number <= end:
                out.append(f"{number}: {line.rstrip()}")
    header = f"{_rel(full)} lines {start}-{min(end, total)} of {total}"
    return header + "\n" + "\n".join(out)


def tool_search_code(args: dict) -> str:
    query = _str_arg(args, "query", "", 200).strip()
    if len(query) < 2:
        raise InvalidInputError("query must be at least 2 characters.")
    start = _resolve(_str_arg(args, "path", "", 300), want_dir=True)
    needle = query.lower()
    hits = []
    for full in _walk_text_files(start):
        try:
            if os.path.getsize(full) > MAX_SEARCH_FILE_BYTES:
                continue
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                for number, line in enumerate(f, 1):
                    if needle in line.lower():
                        hits.append(f"{_rel(full)}:{number}: {line.strip()[:200]}")
                        if len(hits) >= MAX_SEARCH_HITS:
                            break
        except OSError:
            continue
        if len(hits) >= MAX_SEARCH_HITS:
            hits.append("[... more hits not shown; narrow the query or path]")
            break
    return "\n".join(hits) if hits else "No matches."


def _git_env() -> dict:
    # Literal pathspecs: a path like ":(glob)**/[.]env" is just a name.
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                GIT_LITERAL_PATHSPECS="1")


def _git(*args: str) -> str:
    """One read-only git command. Fixed argument lists only (callers
    never pass a model-chosen option), no shell, a timeout, no pager, no
    external diff/textconv drivers and no fsmonitor hook."""
    cmd = ["git", "-c", "core.pager=cat", "-c", "core.fsmonitor=", "-c", "diff.external=",
           "--no-optional-locks", *args]
    env = _git_env()
    try:
        proc = subprocess.run(cmd, cwd=repo_root(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SECONDS,
                              env=env, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise DependencyUnavailableError("git isn't installed or isn't on PATH.") from None
    except subprocess.TimeoutExpired:
        raise ServiceError("The git command took too long and was stopped.") from None
    if proc.returncode != 0:
        raise ServiceError("git failed: " + _redact(proc.stderr.strip())[:300])
    return _clip(proc.stdout, MAX_GIT_OUTPUT_CHARS)


def _ref_arg(args, name, default=None):
    value = _str_arg(args, name, default or "", 100).strip()
    if not value:
        return default
    if not _REF_RE.match(value) or ".." in value:
        raise InvalidInputError(f"{name} must be a branch, tag or commit id.")
    return value


def tool_git_status(args: dict) -> str:
    return _git("status", "--short", "--branch") or "Clean working tree."


def tool_git_log(args: dict) -> str:
    n = _int_arg(args, "n", 20, 1, 100)
    ref = _ref_arg(args, "ref", "HEAD")
    return _git("log", f"--max-count={n}", "--date=short", "--format=%h %ad %s", ref, "--")


def tool_git_diff(args: dict) -> str:
    """Diff of the working tree against `ref` (default HEAD), or between
    `ref` and `to_ref`; optional repo-relative `path`."""
    ref = _ref_arg(args, "ref", "HEAD")
    to_ref = _ref_arg(args, "to_ref", None)
    path = _str_arg(args, "path", "", 300)
    stat = args.get("stat") is True
    cmd = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
    if stat:
        cmd.append("--stat")
    cmd.append(ref)
    if to_ref:
        cmd.append(to_ref)
    cmd.append("--")
    if path:
        full = os.path.realpath(os.path.join(repo_root(), path.replace("\\", "/")))
        # Same rules as reading; a deleted file can't be resolved, so
        # only the name checks apply to one that no longer exists.
        if os.path.exists(full):
            _resolve(path, want_dir=os.path.isdir(full))
        else:
            _resolve_name_only(path)
        cmd.append(_rel(full))
    # No excludes: git diff only covers tracked files (.env, keys and the
    # library are untracked), output is redacted, and literal pathspecs
    # would turn ":(exclude)..." into file names that match nothing.
    return _redact(_git(*cmd)) or "No differences."


def _resolve_name_only(path: str):
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if (path.startswith(("/", ":", "\\")) or re.match(r"^[A-Za-z]:", path)
            or any(p == ".." for p in parts) or _denied_rel(parts, False)):
        raise InvalidInputError(_OUTSIDE)


def tool_inspect_logs(args: dict) -> str:
    n = _int_arg(args, "n", 100, 1, diagnostics_gaps_service.LOG_TAIL_MAX)
    keyword = _str_arg(args, "keyword", "", 100)
    lines = diagnostics_gaps_service.get_log_tail(n, keyword)
    return "\n".join(lines) if lines else "No matching log lines."


def tool_job_history(args: dict) -> str:
    jobs = diagnostics_gaps_service.get_job_history()[:20]
    if not jobs:
        return "No finished jobs in this session."
    return json.dumps(jobs, ensure_ascii=False, default=str)


def tool_support_report(args: dict) -> str:
    return diagnostics_gaps_service.build_support_report()


def tool_check_dependencies(args: dict) -> str:
    deps = diagnostics.check_all_dependencies()
    rows = [f"{name}: {'installed' if info['installed'] else 'MISSING'} ({info['tier']}) -- {info['powers']}"
            for name, info in sorted(deps.items())]
    return "\n".join(rows)


def tool_check_models(args: dict) -> str:
    rows = diagnostics_gaps_service.get_model_versions()
    return "\n".join(f"{r['name']}: {r['version'] if r['installed'] else 'not installed'}"
                     for r in rows)


_KEYISH_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|WEBHOOK|NTFY|CREDENTIAL|^BAIHE_)",
                            re.IGNORECASE)


def _test_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not _KEYISH_ENV_RE.search(k)}
    env["BAIHE_ASSISTANT_TEST_RUN"] = "1"
    return env


_TEST_PATH_RE = re.compile(r"^tests/test_[A-Za-z0-9_]+\.py$")
_TEST_LOCK = threading.Lock()


def tool_run_tests(args: dict) -> str:
    """Runs ONE test file under tests/ (pytest -q), one run at a time and
    at most one per question. The child gets no key-looking environment
    variables, and services/assistant_pytest_guard points its library at
    a throwaway folder and its .env at an empty file before any test
    runs, so a test that forgot isolated_db still can't touch the real
    library or spend with a real key."""
    path = _str_arg(args, "path", "", 200).replace("\\", "/")
    if not _TEST_PATH_RE.match(path):
        raise InvalidInputError("path must be one test file, like tests/test_db.py.")
    _resolve(path, want_dir=False)
    if not _TEST_LOCK.acquire(blocking=False):
        raise ConflictError("A test run is already in progress.")
    try:
        cmd = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider",
               "-p", "services.assistant_pytest_guard", "--no-header", "-rfE", path]
        try:
            proc = subprocess.run(cmd, cwd=repo_root(), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=TEST_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
                                  env=_test_env())
        except subprocess.TimeoutExpired:
            return f"The test run took over {TEST_TIMEOUT_SECONDS} seconds and was stopped."
        output = (proc.stdout or "") + (proc.stderr or "")
        tail = "\n".join(output.strip().splitlines()[-60:])
        return _redact(f"exit code {proc.returncode}\n{tail}")
    finally:
        _TEST_LOCK.release()


# name -> (function, description, argument help). The ONLY tools that
# exist. Every one is read-only; test_maintenance_assistant pins that
# each maps to a 🟢 action tier and that no write-shaped name is here.
READ_ONLY_TOOLS = {
    "list_files": (tool_list_files, "List a project folder.", '{"path": "services"}'),
    "read_file": (tool_read_file, f"Read lines of a source/text file (max {MAX_READ_LINES}).",
                  '{"path": "db.py", "start": 1, "end": 120}'),
    "search_code": (tool_search_code, "Case-insensitive text search across the project's source.",
                    '{"query": "def save_lines", "path": ""}'),
    "git_status": (tool_git_status, "Current branch and uncommitted changes.", "{}"),
    "git_log": (tool_git_log, "Recent commits (hash, date, subject).", '{"n": 20, "ref": "HEAD"}'),
    "git_diff": (tool_git_diff, "Read-only diff: working tree vs ref, or ref vs to_ref.",
                 '{"ref": "HEAD~3", "to_ref": "HEAD", "path": "", "stat": false}'),
    "inspect_logs": (tool_inspect_logs, "The app log's last lines (redacted), keyword-filtered.",
                     '{"n": 100, "keyword": "error"}'),
    "job_history": (tool_job_history, "Finished background jobs in this session (redacted).", "{}"),
    "support_report": (tool_support_report, "The redacted Diagnostics support report.", "{}"),
    "check_dependencies": (tool_check_dependencies, "Optional packages: installed or missing.", "{}"),
    "check_models": (tool_check_models, "Installed model/engine versions.", "{}"),
    "run_tests": (tool_run_tests, "Run one mocked test file and show the result.",
                  '{"path": "tests/test_db.py"}'),
}

# The action_tiers name each tool counts as (all 🟢).
TOOL_ACTIONS = {
    "list_files": "inspect_git_history", "read_file": "inspect_git_history",
    "search_code": "inspect_git_history", "git_status": "inspect_git_history",
    "git_log": "inspect_git_history", "git_diff": "inspect_git_history",
    "inspect_logs": "inspect_logs", "job_history": "inspect_logs",
    "support_report": "generate_report", "check_dependencies": "check_dependencies",
    "check_models": "check_model_availability", "run_tests": "run_tests",
}


def list_tools() -> dict:
    tools = []
    for name, (_fn, description, _example) in READ_ONLY_TOOLS.items():
        tier = action_tiers.classify_action(TOOL_ACTIONS[name])
        tools.append({"name": name, "description": description, "tier": tier.value})
    return {"tools": tools, "write_tools": []}


def run_tool(name: str, args) -> dict:
    """One tool call -> {ok, output}. Never raises for a model mistake:
    an unknown tool or bad argument comes back as ok=False text the model
    can read and correct."""
    entry = READ_ONLY_TOOLS.get(name) if isinstance(name, str) else None
    if entry is None:
        return {"ok": False, "output": f"No such tool: {_redact(str(name)[:60])!r}. "
                                      f"This assistant is read-only; available tools: "
                                      f"{', '.join(READ_ONLY_TOOLS)}."}
    if action_tiers.classify_action(TOOL_ACTIONS[name]) is not action_tiers.ActionTier.GREEN:
        return {"ok": False, "output": "Refused: not a read-only tool."}
    if not isinstance(args, dict):
        args = {}
    try:
        output = entry[0](args)
        return {"ok": True, "output": _clip(_redact(output))}
    except ServiceError as e:
        return {"ok": False, "output": _redact(e.message)[:500]}
    except Exception as e:  # a tool bug must not take the whole answer down
        return {"ok": False, "output": "The tool failed: " + _redact(str(e))[:300]}


# ---------------------------------------------------------------------------
# The chat loop
# ---------------------------------------------------------------------------

_TOOL_LINE_RE = re.compile(r"^\s*TOOL:\s*(\{.*\})\s*$", re.MULTILINE)
_BACKLOG_LINE_RE = re.compile(r"^\s*BACKLOG:\s*(bug|feature|note)\s*\|\s*(.+?)\s*$",
                              re.MULTILINE | re.IGNORECASE)
_PATCH_RE = re.compile(r"```(?:diff|patch)\s*\n(.*?)```", re.DOTALL)
_PATCH_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(\S+)", re.MULTILINE)


def tools_prompt() -> str:
    """How to call the read-only tools; shared by every role (Step 60)."""
    tool_lines = "\n".join(f"- {name}: {desc} Example args: {example}"
                           for name, (_fn, desc, example) in READ_ONLY_TOOLS.items())
    return (
        "To use a tool, reply with one or more lines of exactly this form and nothing "
        "else on those lines:\n"
        'TOOL: {"id": "t1", "name": "<tool>", "args": {...}}\n'
        f"Give each call a new id. At most {MAX_TOOL_CALLS_PER_ROUND} calls per reply. "
        "You will get each result back labelled with its id. When you have enough, "
        "reply with your final answer and no TOOL lines.\n\n"
        f"Tools:\n{tool_lines}"
    )


def _system_prompt() -> str:
    return (
        "You are the maintenance assistant inside Baihe, a subtitle/translation app, "
        "helping its owner diagnose problems in the app itself. You can only READ: "
        "there is no tool that edits files, runs a mutating git command, changes "
        "settings or touches the user's library, and you must not claim to have "
        "changed anything.\n\n"
        + tools_prompt() + "\n\n"
        "Final answer rules: ground every claim in what the tools showed (name the "
        "file and line). If you can't tell, say so plainly instead of guessing. If you "
        "propose a code fix, put it in a ```diff fenced block as a unified diff "
        "(--- a/path, +++ b/path, @@ hunks) against the current files -- the user "
        "reviews and applies it by hand; it is never applied for them. If something "
        "should be tracked for later, add a line 'BACKLOG: bug | <one sentence>' "
        "(or feature/note); the user decides whether to add it."
    )


def _unique_id(wanted: str, used: set) -> str:
    call_id, n = wanted, 1
    while call_id in used:
        n += 1
        call_id = f"{wanted}-{n}"
    used.add(call_id)
    return call_id


def _parse_tool_calls(reply: str, used_ids: set = None) -> list:
    """TOOL lines -> calls, each with an id unique across the whole
    question (`used_ids` carries them between rounds), so every RESULT
    and every tool_calls row is matched by id, never by order."""
    used_ids = set() if used_ids is None else used_ids
    calls = []
    for match in _TOOL_LINE_RE.finditer(reply or ""):
        try:
            obj = json.loads(match.group(1))
        except (ValueError, TypeError):
            obj = None
        if not isinstance(obj, dict):
            calls.append({"id": _unique_id("bad", used_ids), "name": None, "args": {},
                          "malformed": True})
            continue
        call_id = obj.get("id")
        if not isinstance(call_id, str) or not re.match(r"^[A-Za-z0-9_-]{1,20}$", call_id):
            call_id = "call"
        args = obj.get("args")
        calls.append({"id": _unique_id(call_id, used_ids), "name": obj.get("name"),
                      "args": args if isinstance(args, dict) else {}, "malformed": False})
    return calls


def extract_patches(answer: str) -> list:
    patches = []
    for n, match in enumerate(_PATCH_RE.finditer(answer or ""), 1):
        patch = match.group(1).rstrip() + "\n"
        files = sorted({f for f in _PATCH_FILE_RE.findall(patch) if f != "/dev/null"})
        patches.append({"id": f"p{n}", "patch": patch, "files": files})
    return patches


def extract_backlog_suggestions(answer: str) -> list:
    out = []
    for kind, text in _BACKLOG_LINE_RE.findall(answer or ""):
        out.append({"kind": kind.lower(), "text": text.strip()[:MAX_BACKLOG_TEXT]})
    return out[:10]


def _clean_answer(answer: str) -> str:
    return _BACKLOG_LINE_RE.sub("", _TOOL_LINE_RE.sub("", answer or "")).strip()


def _check_history(chat_history) -> list:
    history = chat_history if chat_history is not None else []
    if not isinstance(history, list) or len(history) > MAX_CHAT_TURNS:
        raise InvalidInputError(f"chat_history must be a list of at most {MAX_CHAT_TURNS} turns.")
    for m in history:
        if (not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                or not isinstance(m.get("content"), str) or len(m["content"]) > MAX_CHAT_TURN_CHARS):
            raise InvalidInputError("Each chat_history turn is {role: user|assistant, content: text}"
                                    f" of at most {MAX_CHAT_TURN_CHARS} characters.")
    if sum(len(m["content"]) for m in history) > MAX_CHAT_TOTAL_CHARS:
        raise InvalidInputError(f"chat_history is longer than {MAX_CHAT_TOTAL_CHARS} characters in total.")
    # An empty turn (an answer that was only BACKLOG lines) is dropped:
    # some providers reject empty message content.
    return [{"role": m["role"], "content": m["content"]} for m in history if m["content"].strip()]


def _check_question(question) -> str:
    if not isinstance(question, str) or not question.strip():
        raise InvalidInputError("question is required.")
    if len(question) > MAX_QUESTION_CHARS:
        raise InvalidInputError(f"question must be at most {MAX_QUESTION_CHARS} characters.")
    return question.strip()


def build_engine(engine_name=None, model=None):
    """A chat-capable engine with its key resolved server-side (never
    from the request). Same rules as the Reader's Q&A engine."""
    from services import reader_service
    settings = get_settings()
    saved_engine = settings["engine"] or DEFAULT_ENGINE
    engine_name = _check_engine_name(engine_name) or saved_engine
    model = _check_model(model) or (settings["model"] if engine_name == saved_engine else None)
    from services import line_ai_service, settings_service
    require_cloud_consent(engine_name)
    engine = reader_service._llm_engine(engine_name, model)
    line_ai_service.refuse_if_over_monthly_cap(engine_name, settings_service.get_gemini_free_tier())
    return engine, engine_name, model


def _chat(system_prompt: str, messages: list, engine) -> str:
    import qa
    try:
        return qa._dispatch_chat(system_prompt, messages, engine,
                                 max_tokens=MAX_OUTPUT_TOKENS) or ""
    except ServiceError:
        raise
    except Exception as e:  # engine/network failure: never leak a key or path
        raise ServiceError("The engine call failed: " + _redact(str(e))[:300]) from None


def _shown_args(args: dict) -> dict:
    """The call's args as shown to the user: scalars only, redacted,
    short."""
    shown = {}
    for key, value in list(args.items())[:8]:
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            shown[_redact(str(key)[:40])] = _redact(value)[:200] if isinstance(value, str) else value
    return shown


def run_diagnosis(question: str, history: list, engine, chat=None, system_prompt=None,
                  tests_already_run: bool = False) -> dict:
    """The tool loop. `chat(system_prompt, messages, engine) -> str` is
    injectable for tests. Returns the final answer text and the tool
    calls made (id, name, args, ok, summary). `system_prompt` sets the
    role (Step 60's reviewer); the tool table is the same read-only one
    for every role."""
    chat = chat or _chat
    system_prompt = system_prompt or _system_prompt()
    messages = list(history) + [{"role": "user", "content": question}]
    calls_made = []
    used_ids = set()
    tests_run = tests_already_run
    reply = ""
    for round_no in range(MAX_ROUNDS):
        reply = chat(system_prompt, messages, engine)
        calls = _parse_tool_calls(reply, used_ids)
        if not calls:
            break
        if round_no == MAX_ROUNDS - 1:
            reply = ("I ran out of tool rounds before reaching an answer. Here is what I "
                     "looked at so far; ask a narrower question to continue.")
            break
        results = []
        for call in calls[:MAX_TOOL_CALLS_PER_ROUND]:
            if call["malformed"]:
                outcome = {"ok": False, "output": "That TOOL line wasn't valid JSON."}
            elif call["name"] == "run_tests" and tests_run:
                outcome = {"ok": False, "output": "Only one test run per question."}
            else:
                tests_run = tests_run or call["name"] == "run_tests"
                outcome = run_tool(call["name"], call["args"])
            calls_made.append({
                "id": call["id"], "name": _redact(str(call["name"] or "")[:60]),
                "args": _shown_args(call["args"]),
                "ok": outcome["ok"],
                "summary": outcome["output"].splitlines()[0][:200] if outcome["output"] else "",
            })
            results.append(f"RESULT {call['id']} ({call['name']}, "
                           f"{'ok' if outcome['ok'] else 'failed'}):\n{outcome['output']}")
        if len(calls) > MAX_TOOL_CALLS_PER_ROUND:
            results.append(f"Only the first {MAX_TOOL_CALLS_PER_ROUND} calls ran.")
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": "\n\n".join(results)})
    return {"answer": reply, "tool_calls": calls_made, "tests_run": tests_run}


_ASK_LOCK = threading.Lock()


def ask(question: str, chat_history=None, engine_name: str = None, model: str = None,
        chat=None) -> dict:
    """One assistant turn. Stateless: the client keeps the history."""
    _require_developer_mode()
    question = _check_question(question)
    history = _check_history(chat_history)
    engine, engine_name, model = build_engine(engine_name, model)
    if not _ASK_LOCK.acquire(blocking=False):
        raise ConflictError("The assistant is already answering a question. Try again when it's done.")
    try:
        result = run_diagnosis(question, history, engine, chat=chat)
        raw = _redact(result["answer"])
        answer = _clean_answer(raw)
        if raw.count("```") % 2:
            answer += ("\n\n[The answer looks cut off (an unclosed code block); any fix in it "
                       "is incomplete. Ask for just the patch.]")
        patches = extract_patches(raw)
        review = None
        if patches and get_settings()["roles_enabled"]:
            review = independent_review(question, answer, patches, engine_name, chat=chat,
                                        tests_already_run=result["tests_run"])
    finally:
        _ASK_LOCK.release()
    return {
        "answer": answer,
        "proposed_patches": patches,
        "suggested_backlog": extract_backlog_suggestions(raw),
        "tool_calls": result["tool_calls"],
        "engine": engine_name,
        "model": model,
        "review": review,
    }


# ---------------------------------------------------------------------------
# Step 60: independent cross-provider review of a proposed fix
# ---------------------------------------------------------------------------

def build_review_engine():
    """The review role's engine: the saved review_engine only (never a
    fallback to the implement engine). Returns (engine, name, model) or
    raises ServiceError with a user-facing reason."""
    from services import line_ai_service, reader_service, settings_service
    settings = get_settings()
    name = settings["review_engine"]
    if not name:
        raise ConflictError("No review engine is set. Pick one that differs from the "
                            "implementing engine in the assistant's settings.")
    model = settings["review_model"]
    require_cloud_consent(name)  # the reviewer reads the same code and logs
    engine = reader_service._llm_engine(name, model)
    line_ai_service.refuse_if_over_monthly_cap(name, settings_service.get_gemini_free_tier())
    return engine, name, model


def independent_review(question: str, answer: str, patches: list, implement_engine: str,
                       chat=None, tests_already_run: bool = False) -> dict:
    """Runs the review role on a DIFFERENT engine and returns
    {engine, model, verdict, notes, tool_calls}. Never raises: a review
    that can't run comes back as verdict "unavailable" with the reason,
    and the proposed fix is still shown -- nothing is resolved silently."""
    from services import assistant_roles_service as roles
    try:
        engine, name, model = build_review_engine()
        if roles.same_backend(implement_engine, name):
            return {"engine": name, "model": model, "verdict": "unavailable", "tool_calls": [],
                    "notes": (f"The review engine ({name}) is the same as the implementing "
                              "engine. Pick a different engine for an independent review.")}
        result = run_diagnosis(roles.review_request(question, answer, patches), [], engine,
                               chat=chat, system_prompt=roles.review_system_prompt(tools_prompt()),
                               tests_already_run=tests_already_run)
    except ServiceError as e:
        return {"engine": get_settings()["review_engine"], "model": None, "verdict": "unavailable",
                "notes": _redact(e.message)[:500], "tool_calls": []}
    except Exception as e:  # e.g. an engine whose optional package isn't installed
        return {"engine": get_settings()["review_engine"], "model": None, "verdict": "unavailable",
                "notes": "The review engine couldn't run: " + _redact(str(e))[:300],
                "tool_calls": []}
    verdict, notes = roles.parse_verdict(_redact(result["answer"]))
    return {"engine": name, "model": model, "verdict": verdict,
            "notes": _clean_answer(notes), "tool_calls": result["tool_calls"]}


# ---------------------------------------------------------------------------
# Changelog (roadmap item 8)
# ---------------------------------------------------------------------------

MAX_CHANGELOG_COMMITS = 300


def changelog(from_ref: str, to_ref: str = "HEAD", engine_name: str = None, model: str = None,
              chat=None) -> dict:
    """A plain-English summary of the commits in from_ref..to_ref. Uses
    commit subjects (bodies would crowd out older commits); the count
    comes from rev-list, and `truncated` says when the newest
    MAX_CHANGELOG_COMMITS were all the model saw."""
    _require_developer_mode()
    from_ref = _ref_arg({"r": from_ref}, "r")
    to_ref = _ref_arg({"r": to_ref or "HEAD"}, "r", "HEAD")
    if not from_ref:
        raise InvalidInputError("from_ref is required.")
    span = f"{from_ref}..{to_ref}"
    try:
        commit_count = int(_git("rev-list", "--count", span, "--").strip() or 0)
    except ValueError:
        commit_count = 0
    if commit_count == 0:
        return {"changelog": "No commits in that range.", "commit_count": 0,
                "from_ref": from_ref, "to_ref": to_ref, "truncated": False}
    log = _git("log", f"--max-count={MAX_CHANGELOG_COMMITS}", "--date=short",
               "--format=%h %ad %s", span, "--")
    shown = len([ln for ln in log.splitlines() if ln.strip()])
    truncated = shown < commit_count
    engine, _name, _model = build_engine(engine_name, model)
    system_prompt = (
        "Write release notes for the owner of Baihe (a subtitle/translation app) from the "
        "git commit subjects below. Group into short sections (New, Fixed, Changed, Under the "
        "hood), plain English, one line per user-visible change, merge commits that are "
        "parts of one change, skip pure test/doc churn unless it matters. Don't invent "
        "anything that isn't in the commits.")
    text = (chat or _chat)(system_prompt, [{"role": "user", "content": _redact(log)}], engine)
    return {"changelog": _redact(text).strip(), "commit_count": commit_count,
            "from_ref": from_ref, "to_ref": to_ref, "truncated": truncated}


# ---------------------------------------------------------------------------
# Backlog (roadmap item 7): written by the user only
# ---------------------------------------------------------------------------

def list_backlog() -> dict:
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT id, kind, text, created_at FROM assistant_backlog "
                            "ORDER BY id DESC").fetchall()
    return {"items": [{"id": r["id"], "kind": r["kind"], "text": r["text"],
                       "created_at": r["created_at"]} for r in rows]}


def add_backlog_item(kind: str, text: str) -> dict:
    if kind not in BACKLOG_KINDS:
        raise InvalidInputError(f"kind must be one of {', '.join(BACKLOG_KINDS)}.")
    if not isinstance(text, str) or not text.strip():
        raise InvalidInputError("text is required.")
    if len(text) > MAX_BACKLOG_TEXT:
        raise InvalidInputError(f"text must be at most {MAX_BACKLOG_TEXT} characters.")
    text = _redact(text.strip())
    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    with contextlib.closing(db.get_conn()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM assistant_backlog").fetchone()[0]
        if count >= MAX_BACKLOG_ITEMS:
            raise ConflictError(f"The backlog is full ({MAX_BACKLOG_ITEMS} items). Delete some first.")
        cur = conn.execute("INSERT INTO assistant_backlog (kind, text, created_at) VALUES (?, ?, ?)",
                           (kind, text, created))
        conn.commit()
        item_id = cur.lastrowid
    return {"id": item_id, "kind": kind, "text": text, "created_at": created}


def delete_backlog_item(item_id: int, confirm: bool = False) -> dict:
    if confirm is not True:
        raise InvalidInputError("Deleting a backlog item needs confirm=true.")
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("DELETE FROM assistant_backlog WHERE id = ?", (int(item_id),))
        conn.commit()
    if cur.rowcount == 0:
        raise NotFoundError("No such backlog item.")
    return {"deleted": True}


def clear_backlog(confirm: bool = False) -> dict:
    if confirm is not True:
        raise InvalidInputError("Clearing the backlog needs confirm=true.")
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("DELETE FROM assistant_backlog")
        conn.commit()
    return {"deleted": cur.rowcount}

