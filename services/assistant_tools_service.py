"""
services/assistant_tools_service.py -- the maintenance assistant's fixed table
of READ-ONLY tools (list/read/search the code, read-only git, log, job
history, source failures, support report, dependency and model checks, one
test file) and the single entry point that runs one. UI-free.
"""

import json
import os
import re
import sys
import threading
import time

import action_tiers
import diagnostics
from lib import proc as proc_run
from services import diagnostics_gaps_service
from services.assistant_repo_access import (_OUTSIDE, _SECRET_NAME_RE, _clip, _denied_rel, _git, _is_denied_part,
                                            _redact, _rel, _resolve, _tracked_files, _walk_text_files,
                                            repo_root)
from lib.errors import ConflictError, InvalidInputError, ServiceError


MAX_READ_LINES = 400
MAX_LIST_ENTRIES = 300
MAX_SEARCH_HITS = 80
MAX_SEARCH_FILE_BYTES = 1_000_000
TEST_TIMEOUT_SECONDS = 300
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^-]{0,99}$")


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
        if tracked is not None and tracked_dirs is not None and rel not in tracked and rel not in tracked_dirs:
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
    if not path:
        # Obey the deny-list: drop changed tracked files the assistant may
        # not read (secret-named files, dot-dirs) and name the rest.
        names_cmd = ["diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", ref]
        if to_ref:
            names_cmd.append(to_ref)
        names = [n for n in _git(*names_cmd, "--").split("\0") if n]
        allowed = [n for n in names if not _denied_rel([x for x in n.split("/") if x], False)]
        if len(allowed) != len(names):
            if not allowed:
                return "No differences."
            cmd.extend(allowed)
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


def tool_source_failures(args: dict) -> str:
    """Category, source name, streak count and last-seen time only: no URLs,
    page content or stored error text. Only registered source names, so a
    pasted-URL host never appears."""
    from sources import health, registry
    known = set(registry.adapter_classes())
    rows = [r for r in health.recent_failures(50) if r["source"] in known][:20]
    if not rows:
        return "No recent source failures."
    return "\n".join(
        f"{r['source']}: {r['category']} x{r['count']}, last seen "
        f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['last_failure']))} UTC" for r in rows)


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
        proc = proc_run.run_captured(cmd, TEST_TIMEOUT_SECONDS, cwd=repo_root(),
                                            env=_test_env())
        if proc.timed_out:
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
    "source_failures": (tool_source_failures,
                        "Recent content-source failures by category (no URLs or page text).", "{}"),
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
    "source_failures": "inspect_logs",
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
