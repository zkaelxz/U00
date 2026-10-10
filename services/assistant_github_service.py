"""
services/assistant_github_service.py -- deliver a maintenance-assistant
proposed fix as a real GitHub pull request. UI-free;
api/routers/assistant_github_routes.py (every route PC-only) calls it.

Hard rules, each enforced here, not only in the UI:
- Off by default. While disabled, or with no token, or with no repo set,
  no GitHub call of any kind is made (every network function goes through
  _require_ready first).
- The token is a key: kept in .env as BAIHE_GITHUB_TOKEN (the same
  settings_service writer the engine keys use), sent only as an
  Authorization header, never returned, logged, put in a URL or an error;
  every error text passes through _scrub (the token itself, plus
  translate_engines.redact_secrets and GitHub token patterns).
- Never pushes to an existing branch: the only ref write is POST
  /git/refs creating a brand-new `baihe-assistant/...` branch; there is
  no PATCH/PUT of a ref anywhere. The PR is a draft into the configured
  base (default baihe-subtitler), which it never writes.
- The exact diff is shown before delivery (preview returns it with its
  sha256), and delivery needs confirm=true plus that same sha256, so
  every PR is one explicit yes for one exact diff. action_tiers counts
  this as publish_to_shared_target (RED): require_confirmation runs on
  every delivery.
- Nothing that runs before review: a patch may not touch .github/ (CI
  workflows), .claude/ or CLAUDE.md (they steer any Claude Code session
  on the branch), or the entry points and config that CI, python, pytest,
  npm, PostCSS or the launcher load from the PR head; see
  _runs_before_review for the exact list. Hunk lines with invisible or
  text-direction control characters are refused too.
- Out of scope (roadmap item 4): reading or triaging other issues/PRs.
"""

import base64
import hashlib
import json as _json
import re
import threading
import time

import requests

import action_tiers
import db
from lib import capped_body
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, ServiceError)

API = "https://api.github.com"
TIMEOUT = 20
MAX_RESPONSE_BYTES = 5_000_000
TOKEN_ENV = ("BAIHE_GITHUB_TOKEN",)
BRANCH_PREFIX = "baihe-assistant/"
DEFAULT_BASE = "baihe-subtitler"
MAX_PATCH_CHARS = 200_000
MAX_FILES = 20
MAX_TITLE = 200
MAX_BODY = 20_000
MAX_HUNK_DRIFT = 20  # lines a hunk may sit from its @@ header's line
_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_./ -]{0,299}$")
_SETTINGS_PREFIX = "assistant.github."
_TOKEN_PATTERNS = [re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")]
_OUTSIDE_PATH = "Every file in the patch must be a plain repo-relative path."


# ---------------------------------------------------------------------------
# Settings and token (no network)
# ---------------------------------------------------------------------------

def _get(key, default=None):
    try:
        return db.get_app_setting(_SETTINGS_PREFIX + key, default)
    except Exception:
        return default


def _settings_service():
    from services import settings_service
    return settings_service


def _token():
    return _settings_service().resolve_env_names(TOKEN_ENV)


def get_status() -> dict:
    """Booleans and names only -- never the token."""
    repo = _get("repo")
    base = _get("base_branch")
    return {
        "enabled": _get("enabled", False) is True,
        "repo": repo if isinstance(repo, str) and _REPO_RE.match(repo) else None,
        "base_branch": base if isinstance(base, str) and _BRANCH_RE.match(base) else DEFAULT_BASE,
        "token_configured": bool(_token()),
        "branch_prefix": BRANCH_PREFIX,
    }


def set_settings(updates: dict) -> dict:
    if not isinstance(updates, dict):
        raise InvalidInputError("Settings must be an object.")
    cleaned = {}
    for key, value in updates.items():
        if key == "enabled":
            if not isinstance(value, bool):
                raise InvalidInputError("enabled must be true or false.")
            cleaned[key] = value
        elif key == "repo":
            if value in (None, ""):
                cleaned[key] = None
            elif not isinstance(value, str) or not _REPO_RE.match(value.strip()):
                raise InvalidInputError("repo must look like owner/name.")
            else:
                cleaned[key] = value.strip()
        elif key == "base_branch":
            if value in (None, ""):
                cleaned[key] = None
            elif (not isinstance(value, str) or not _BRANCH_RE.match(value.strip())
                  or ".." in value or value.strip().startswith(BRANCH_PREFIX)):
                raise InvalidInputError("base_branch must be a branch name.")
            else:
                cleaned[key] = value.strip()
        else:
            raise InvalidInputError("Unknown GitHub setting.")
    for key, value in cleaned.items():
        db.set_app_setting(_SETTINGS_PREFIX + key, value)
    return get_status()


def set_token(value) -> dict:
    """Stored in .env like an engine key; returns configured only."""
    s = _settings_service()
    value = s._validate_key_value(value)
    s.write_env_var(TOKEN_ENV[0], value)
    return {"token_configured": bool(_token())}


def clear_token() -> dict:
    _settings_service().remove_env_vars(TOKEN_ENV)
    return {"token_configured": bool(_token())}


def _require_ready() -> tuple:
    """(token, repo, base) or ConflictError. Called before ANY network
    call, so a disabled integration or a missing token never reaches
    GitHub."""
    status = get_status()
    if not status["enabled"]:
        raise ConflictError("GitHub delivery is off. Turn it on in the assistant's GitHub settings.")
    token = _token()
    if not token:
        raise ConflictError("No GitHub token is set.")
    if not status["repo"]:
        raise ConflictError("No GitHub repository is set.")
    return token, status["repo"], status["base_branch"]


# ---------------------------------------------------------------------------
# HTTP (every call: fixed host, token in a header, a timeout, scrubbed errors)
# ---------------------------------------------------------------------------

def _scrub(text, token=None) -> str:
    import translate_engines
    text = "" if text is None else str(text)
    if token:
        text = text.replace(token, "[REDACTED]")
    text = translate_engines.redact_secrets(text) or ""
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _call(token: str, method: str, path: str, *, json=None, params=None, ok=(200, 201)):
    if not path.startswith("/repos/") and path != "/user":
        raise ServiceError("Refused an unexpected GitHub API path.")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "baihe-maintenance-assistant"}
    try:
        resp = requests.request(method, API + path, headers=headers, json=json, params=params,
                               timeout=TIMEOUT, allow_redirects=False, stream=True)
    except Exception as e:
        raise DependencyUnavailableError("Couldn't reach GitHub: " + _scrub(e, token)[:200]) from None

    def too_big():
        return ServiceError("GitHub sent back more data than expected.")
    raw = capped_body.read_capped(resp, MAX_RESPONSE_BYTES, TIMEOUT * 3, too_big)
    if resp.status_code not in ok:
        detail = ""
        try:
            detail = str(_json.loads(raw).get("message", ""))
        except Exception:
            pass
        raise ServiceError(f"GitHub said {resp.status_code}"
                           + (f": {_scrub(detail, token)[:200]}" if detail else "") + ".")
    try:
        return _json.loads(raw)
    except Exception:
        return {}


def _q(part: str) -> str:
    from urllib.parse import quote
    return quote(part, safe="/")


def test_connection() -> dict:
    token, repo, base = _require_ready()
    info = _call(token, "GET", f"/repos/{repo}")
    perms = info.get("permissions") or {}
    base_exists = True
    try:
        _call(token, "GET", f"/repos/{repo}/branches/{_q(base)}")
    except ServiceError:
        base_exists = False
    return {"ok": True, "repo": repo, "default_branch": info.get("default_branch"),
            "can_push": bool(perms.get("push")), "base_branch": base, "base_exists": base_exists}


# ---------------------------------------------------------------------------
# Unified diff parsing and applying (pure Python, strict)
# ---------------------------------------------------------------------------

_UNSUPPORTED_MARKERS = ("Binary files ", "GIT binary patch", "old mode ", "new mode ",
                        "rename from ", "rename to ", "copy from ", "copy to ",
                        "similarity index ", "new file mode 120000", "new file mode 160000",
                        "deleted file mode 120000", "deleted file mode 160000")
# Ordinary `git diff` header lines that carry no content change.
_GIT_HEADER_LINES = ("diff --git ", "index ", "new file mode 100", "deleted file mode 100")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _clean_path(raw: str):
    raw = raw.strip().split("\t")[0]
    if raw == "/dev/null":
        return None
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    parts = raw.split("/")
    if (not _PATH_RE.match(raw) or raw.startswith("/") or any(p in ("", ".", "..") for p in parts)
            or any(p.lower() == ".git" for p in parts)):
        raise InvalidInputError(_OUTSIDE_PATH)
    if _runs_before_review(parts):
        raise InvalidInputError(f"{raw} runs before anyone reviews the pull request (CI, "
                                "Claude Code or the launcher), so it can't be changed from "
                                "the app; make that change by hand.")
    return raw


# Paths whose content runs, or steers an AI session, as soon as the branch
# exists, before anyone reviews the PR: CI workflows (.github/) run with the
# repo's CI token; .claude/ (the SessionStart hook) and CLAUDE.md steer any
# Claude Code session that checks the branch out; and the CI entry points
# .github/workflows/*.yml run from the PR head (the test runner, pytest
# config and conftest.py, the pip requirement/constraint files, the npm
# manifest and lockfile, the frontend build/test tool configs, start.bat).
# Compared case-insensitively.
_BLOCKED_DIRS = {".github", ".claude"}                  # at any depth
# At any depth: interpreter, test-runner and package-manager config that
# python, pytest, tox or npm pick up from the working directory.
_BLOCKED_NAMES_ANY_DEPTH = {"claude.md", "claude.local.md", "conftest.py", ".npmrc",
                            "sitecustomize.py", "usercustomize.py", "pytest.ini", ".pytest.ini",
                            "tox.ini", "setup.cfg", "pyproject.toml"}
_BLOCKED_ANY_DEPTH_RE = re.compile(r"^(?:postcss\.config\..+|\.postcssrc.*)$")
_BLOCKED_ROOT_FILES = {"run_tests.py", "start.bat", "check_setup.py", ".mcp.json"}
_BLOCKED_FRONTEND_FILES = {"package.json", "package-lock.json", ".oxlintrc.json"}
_BLOCKED_FRONTEND_CONFIG = re.compile(r"^(?:(?:vite|vitest|playwright|eslint)\.config\.[a-z]+|tsconfig[^/]*\.json)$")
_REQUIREMENTS_RE = re.compile(r"^(?:requirements|constraints)[^/]*\.txt$")
# Characters that make a line read differently from what it does.
_HIDDEN_CHARS_RE = re.compile("[\u202a-\u202e\u2066-\u2069\u200b-\u200d\u2060\ufeff]")


def _runs_before_review(parts: list) -> bool:
    low = [p.lower() for p in parts]
    name = low[-1]
    if (any(p in _BLOCKED_DIRS for p in low) or name in _BLOCKED_NAMES_ANY_DEPTH
            or _BLOCKED_ANY_DEPTH_RE.match(name)):
        return True
    if len(low) == 1:
        return name in _BLOCKED_ROOT_FILES or bool(_REQUIREMENTS_RE.match(name))
    if low[0] == "frontend":
        if len(low) > 2 and low[1] == "scripts":
            return True
        if len(low) == 2:
            return name in _BLOCKED_FRONTEND_FILES or bool(_BLOCKED_FRONTEND_CONFIG.match(name))
    return False


def parse_patch(patch: str) -> list:
    """[{old, new, hunks: [{old_start, lines: [(' '|'-'|'+', text)], no_eol_old, no_eol_new}]}]."""
    if not isinstance(patch, str) or not patch.strip():
        raise InvalidInputError("The patch is empty.")
    if len(patch) > MAX_PATCH_CHARS:
        raise InvalidInputError("The patch is too large.")
    files, cur = [], None
    lines = patch.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            cur = {"old": _clean_path(line[4:]), "new": _clean_path(lines[i + 1][4:]), "hunks": []}
            if cur["old"] is None and cur["new"] is None:
                raise InvalidInputError(_OUTSIDE_PATH)
            if cur["old"] and cur["new"] and cur["old"] != cur["new"]:
                raise InvalidInputError("Renames aren't supported; use a delete and an add.")
            files.append(cur)
            i += 2
            continue
        if line.startswith(_UNSUPPORTED_MARKERS):
            raise InvalidInputError("The patch has a binary, mode, rename or copy change, which "
                                    "can't be delivered from the app.")
        m = _HUNK_RE.match(line)
        if m and cur is not None:
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(4)) if m.group(4) is not None else 1
            hunk = {"old_start": int(m.group(1)), "lines": [], "no_eol_old": False,
                    "no_eol_new": False}
            i += 1
            # Exactly the lines the header counts (a bare empty line is a
            # blank context line some tools emit without its space).
            while (old_left > 0 or new_left > 0) and i < len(lines):
                body = lines[i]
                kind = body[:1] if body else " "
                if kind == "\\":  # "\\ No newline at end of file" for the line above
                    last = hunk["lines"][-1][0] if hunk["lines"] else " "
                    hunk["no_eol_old"] = hunk["no_eol_old"] or last in (" ", "-")
                    hunk["no_eol_new"] = hunk["no_eol_new"] or last in (" ", "+")
                    i += 1
                    continue
                if kind not in (" ", "-", "+"):
                    raise InvalidInputError("A hunk in the patch is shorter than its @@ header says.")
                if _HIDDEN_CHARS_RE.search(body):
                    raise InvalidInputError("The patch has an invisible or text-direction "
                                            "control character, which can't be delivered.")
                hunk["lines"].append((kind, body[1:]))
                if kind in (" ", "-"):
                    old_left -= 1
                if kind in (" ", "+"):
                    new_left -= 1
                i += 1
            if old_left != 0 or new_left != 0:
                raise InvalidInputError("A hunk in the patch doesn't match its @@ header.")
            while i < len(lines) and lines[i].startswith("\\"):
                last = hunk["lines"][-1][0] if hunk["lines"] else " "
                hunk["no_eol_old"] = hunk["no_eol_old"] or last in (" ", "-")
                hunk["no_eol_new"] = hunk["no_eol_new"] or last in (" ", "+")
                i += 1
            cur["hunks"].append(hunk)
            continue
        # Nothing may hide between or after the files: what is shown must be
        # exactly what is delivered.
        if cur is not None and line.strip() and not line.startswith(_GIT_HEADER_LINES):
            raise InvalidInputError("The patch has lines outside its files' hunks. "
                                    "Ask the assistant for a clean unified diff.")
        i += 1
    if not files:
        raise InvalidInputError("That isn't a unified diff (no ---/+++ file headers).")
    if len(files) > MAX_FILES:
        raise InvalidInputError(f"At most {MAX_FILES} files per pull request.")
    paths = [f["new"] or f["old"] for f in files]
    if len(set(paths)) != len(paths):
        raise InvalidInputError("A file appears twice in the patch.")
    for f in files:
        if not f["hunks"]:
            raise InvalidInputError("A file in the patch has no changes.")
    return files


def change_kind(f: dict) -> str:
    return "add" if f["old"] is None else "delete" if f["new"] is None else "modify"


def apply_file_patch(old_text, f: dict) -> str:
    """New file content, or None for a delete. Every hunk's context and
    removed lines must match the current file exactly (at the stated line
    or one unique place within MAX_HUNK_DRIFT lines of it); otherwise nothing
    is delivered."""
    kind = change_kind(f)
    if kind == "add":
        if old_text is not None:
            raise ConflictError(f"{f['new']} already exists on the base branch.")
        new_lines = [t for h in f["hunks"] for k, t in h["lines"] if k in (" ", "+")]
        no_eol = f["hunks"][-1]["no_eol_new"]
        return "\n".join(new_lines) + ("" if no_eol else "\n")
    if old_text is None:
        raise ConflictError(f"{f['old']} doesn't exist on the base branch.")
    had_eol = old_text.endswith("\n")
    lines = old_text.split("\n")
    if had_eol:
        lines = lines[:-1]
    offset = 0
    floor = 0  # a hunk may only match after the previous hunk's end
    stale = ConflictError(f"The patch doesn't match the current {f['old']} on the base "
                          "branch. Ask the assistant for a fresh patch.")
    for h in f["hunks"]:
        old_block = [t for k, t in h["lines"] if k in (" ", "-")]
        new_block = [t for k, t in h["lines"] if k in (" ", "+")]
        if not old_block:
            # A context-free insert can't be checked against the file.
            raise ConflictError(f"A change to {f['old']} has no context lines to check it "
                                "against. Ask the assistant for a patch with context.")
        stated = max(0, h["old_start"] - 1 + offset)
        want = stated
        if want < floor or lines[want:want + len(old_block)] != old_block:
            spots = [i for i in range(floor, len(lines) - len(old_block) + 1)
                     if lines[i:i + len(old_block)] == old_block]
            if len(spots) != 1:
                raise stale
            want = spots[0]
            # A unique match far from where the hunk says it goes is more
            # likely the wrong place than a shifted file.
            if abs(want - stated) > MAX_HUNK_DRIFT:
                raise ConflictError(
                    f"A change to {f['old']} says line {h['old_start']}, but its lines only "
                    f"match {abs(want - stated)} lines away (at most {MAX_HUNK_DRIFT}). "
                    "Ask the assistant for a fresh patch.")
        lines[want:want + len(old_block)] = new_block
        offset += len(new_block) - len(old_block)
        floor = want + len(new_block)
    if kind == "delete":
        if any(ln for ln in lines):
            raise ConflictError(f"The patch deletes {f['old']} but doesn't remove all of it.")
        return None
    no_eol = f["hunks"][-1]["no_eol_new"] or (not had_eol and not f["hunks"][-1]["no_eol_old"])
    return "\n".join(lines) + ("" if no_eol else "\n")


# ---------------------------------------------------------------------------
# Preview and delivery
# ---------------------------------------------------------------------------

def _normalize(patch: str) -> str:
    return patch.replace("\r\n", "\n").rstrip("\n") + "\n"


def patch_sha256(patch: str, repo: str = "", base: str = "") -> str:
    """Binds the confirmation to this exact diff AND the repo and base it
    was shown going to: changing either after the preview needs a new one."""
    text = f"{repo}\n{base}\n{_normalize(patch)}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_title(title) -> str:
    if not isinstance(title, str) or not title.strip():
        raise InvalidInputError("A title is required.")
    title = " ".join(title.split())
    if len(title) > MAX_TITLE:
        raise InvalidInputError(f"The title must be at most {MAX_TITLE} characters.")
    return title


def _branch_name(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "fix"
    return f"{BRANCH_PREFIX}{slug}-{time.strftime('%Y%m%d-%H%M%S')}"


def preview(patch: str, title: str) -> dict:
    """What delivery would send: the exact diff, its sha256 (delivery
    must echo it), the files and the base. Makes no network call."""
    _require_ready()
    title = _check_title(title)
    files = parse_patch(patch)
    status = get_status()
    return {
        "repo": status["repo"], "base_branch": status["base_branch"],
        "branch_prefix": BRANCH_PREFIX, "title": title,
        "files": [{"path": f["new"] or f["old"], "change": change_kind(f)} for f in files],
        "patch": _normalize(patch),
        "sha256": patch_sha256(patch, status["repo"], status["base_branch"]),
    }


_DELIVER_LOCK = threading.Lock()


def deliver(patch: str, title: str, body: str = "", sha256: str = None,
            confirm: bool = False) -> dict:
    """Creates one NEW branch and one DRAFT pull request into the base.
    Needs confirm=true and the sha256 the preview showed for this exact
    patch. Never writes an existing branch."""
    if confirm is not True:
        raise InvalidInputError("Delivering a pull request needs confirm=true.")
    action_tiers.require_confirmation("publish_to_shared_target",
                                      action_tiers.Confirmation.EXPLICIT_RED_CONFIRMED)
    token, repo, base = _require_ready()
    title = _check_title(title)
    if not isinstance(body, str) or len(body) > MAX_BODY:
        raise InvalidInputError(f"The description must be at most {MAX_BODY} characters.")
    files = parse_patch(patch)
    if not isinstance(sha256, str) or sha256 != patch_sha256(patch, repo, base):
        raise ConflictError("This patch, repository or base branch isn't the one you previewed. "
                            "Preview it again.")
    branch = _branch_name(title)
    if branch == base or not branch.startswith(BRANCH_PREFIX):
        raise ServiceError("Refused: the new branch name isn't a fresh assistant branch.")
    if not _DELIVER_LOCK.acquire(blocking=False):
        raise ConflictError("A pull request is already being delivered.")
    try:
        return _deliver(token, repo, base, branch, title, body, files)
    finally:
        _DELIVER_LOCK.release()


def _deliver(token, repo, base, branch, title, body, files) -> dict:
    base_sha = _call(token, "GET", f"/repos/{repo}/git/ref/heads/{_q(base)}")["object"]["sha"]
    base_tree = _call(token, "GET", f"/repos/{repo}/git/commits/{base_sha}")["tree"]["sha"]
    tree = _call(token, "GET", f"/repos/{repo}/git/trees/{base_tree}", params={"recursive": "1"})
    if tree.get("truncated"):
        raise ConflictError("The repository is too large to check safely through the API.")
    entries_by_path = {e.get("path"): e for e in tree.get("tree", [])}
    modes = {p: e.get("mode") for p, e in entries_by_path.items() if e.get("type") == "blob"}
    entries = []
    for f in files:
        path = f["new"] or f["old"]
        old_text = None
        if f["old"] is not None:
            try:
                got = _call(token, "GET", f"/repos/{repo}/contents/{_q(path)}", params={"ref": base_sha})
            except ServiceError:
                got = None
            if got is not None:
                if (not isinstance(got, dict) or got.get("type") != "file"
                        or got.get("encoding") != "base64"):
                    raise ConflictError(f"{path} can't be patched through the API (not a small text file).")
                try:
                    old_text = base64.b64decode(got.get("content", "")).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    raise ConflictError(f"{path} isn't a UTF-8 text file.") from None
        else:
            # An add must not land on an existing file or folder, or under a file.
            prefixes = ["/".join(path.split("/")[:k]) for k in range(1, path.count("/") + 1)]
            if path in entries_by_path or any(modes.get(p) for p in prefixes):
                raise ConflictError(f"{path} already exists on the base branch.")
        new_text = apply_file_patch(old_text, f)
        mode = modes.get(path, "100644")
        if mode not in ("100644", "100755"):
            raise ConflictError(f"{path} isn't a regular file.")
        entries.append({"path": path, "mode": mode, "type": "blob",
                        **({"sha": None} if new_text is None else {"content": new_text})})
    new_tree = _call(token, "POST", f"/repos/{repo}/git/trees",
                     json={"base_tree": base_tree, "tree": entries})["sha"]
    commit = _call(token, "POST", f"/repos/{repo}/git/commits",
                   json={"message": title, "tree": new_tree, "parents": [base_sha]})["sha"]
    # The only ref write: CREATE a brand-new branch (422 if it exists).
    _call(token, "POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})
    pr_body = ("Proposed by Baihe's maintenance assistant and delivered from the app after "
               "the owner reviewed this exact diff. Review before merging.\n\n" + (body or "")).strip()
    try:
        pr = _call(token, "POST", f"/repos/{repo}/pulls",
                   json={"title": title, "head": branch, "base": base, "body": pr_body,
                         "draft": True})
    except ServiceError as e:
        # The app never deletes a ref, so the new branch stays: say which.
        raise ServiceError(f"The branch {branch} was created, but opening the pull request "
                           f"failed ({e.message}) You can open it on GitHub from that branch, "
                           "or delete the branch there.") from None
    url = pr.get("html_url") or ""
    if not url.startswith("https://github.com/"):
        url = ""
    return {"pr_url": url, "pr_number": pr.get("number"), "branch": branch, "base_branch": base,
            "repo": repo, "files": [{"path": f["new"] or f["old"], "change": change_kind(f)}
                                    for f in files]}
