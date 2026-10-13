"""
services/assistant_repo_access.py -- what the maintenance assistant may
see: secret redaction, the repo-relative path rules (deny-list, git-tracked
files only, nothing in the library folder) and the one read-only git runner
its tools share. UI-free; the tools are in services/assistant_tools_service.py.
"""

import os
import re
import subprocess

import db
from services.service_errors import DependencyUnavailableError, InvalidInputError, NotFoundError, ServiceError


MAX_TOOL_OUTPUT_CHARS = 12000
MAX_GIT_OUTPUT_CHARS = 20000
GIT_TIMEOUT_SECONDS = 20

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
_ALLOWED_DOT_DIRS = frozenset({".claude", ".github"})
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
    prefixes. Deliberately NOT diagnostics_report.redact_for_support: that one
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
