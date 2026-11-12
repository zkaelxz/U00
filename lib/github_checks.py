"""Finds the newest commit whose CI check passed, from GitHub's public check-runs API."""

import json
import re

from lib import http

TIMEOUT = 10
MAX_BYTES = 512 * 1024
# Unauthenticated requests: GitHub allows 60 an hour per address, so a long
# run of red or unchecked commits is not walked to the end.
MAX_COMMITS = 20
_REPO_RE = re.compile(r"(?:github\.com[:/])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_SHA_RE = re.compile(r"[0-9a-f]{40}")


class CheckUnavailable(Exception):
    """GitHub couldn't be asked or gave an answer that can't be used."""


def repo_from_remote(url: str):
    """owner/name of a github.com remote URL (https or ssh), else None."""
    m = _REPO_RE.search((url or "").strip())
    return m.group(1) if m and ".." not in m.group(1) else None


def passed(repo: str, sha: str, check: str = "test") -> bool:
    """Whether the newest GitHub Actions run of `check` at `sha` succeeded."""
    if not _SHA_RE.fullmatch(sha) or repo_from_remote(f"github.com/{repo}") != repo:
        raise CheckUnavailable()
    try:
        resp = http.get(f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs",
                        params={"check_name": check, "per_page": 100}, timeout=TIMEOUT,
                        max_bytes=MAX_BYTES, guard=None,
                        headers={"Accept": "application/vnd.github+json"})
    except http.FetchError:
        raise CheckUnavailable() from None
    if not resp.ok:
        raise CheckUnavailable()
    try:
        runs = json.loads(resp.body.decode("utf-8")).get("check_runs")
    except (ValueError, AttributeError):
        raise CheckUnavailable() from None
    runs = [r for r in runs or [] if isinstance(r, dict)
            and (r.get("app") or {}).get("slug") == "github-actions"]
    if not runs:
        return False
    newest = max(runs, key=lambda r: str(r.get("started_at") or ""))
    return newest.get("conclusion") == "success"


def newest_passing(repo: str, shas, check: str = "test"):
    """The first of `shas` (newest first) whose check passed, or None."""
    for sha in list(shas)[:MAX_COMMITS]:
        if passed(repo, sha, check):
            return sha
    return None
