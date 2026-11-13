"""Finds the newest commit whose Tests run on baihe-subtitler passed, from GitHub's public Actions API."""

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
WORKFLOW = "tests.yml"
BRANCH = "baihe-subtitler"


class CheckUnavailable(Exception):
    """GitHub couldn't be asked or gave an answer that can't be used."""


def repo_from_remote(url: str):
    """owner/name of a github.com remote URL (https or ssh), else None."""
    m = _REPO_RE.search((url or "").strip())
    return m.group(1) if m and ".." not in m.group(1) else None


def passed(repo: str, sha: str) -> bool:
    """Whether the newest push run of the Tests workflow on baihe-subtitler at
    `sha` succeeded. A pull request's run is left out: it tests the branch
    merged with the base of that moment, not this commit, and anyone can open
    one."""
    if not _SHA_RE.fullmatch(sha) or repo_from_remote(f"github.com/{repo}") != repo:
        raise CheckUnavailable()
    try:
        resp = http.get(f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/runs",
                        params={"head_sha": sha, "event": "push", "branch": BRANCH,
                                "per_page": 100},
                        timeout=TIMEOUT, max_bytes=MAX_BYTES, guard=None,
                        headers={"Accept": "application/vnd.github+json"})
    except http.FetchError:
        raise CheckUnavailable() from None
    if not resp.ok:
        raise CheckUnavailable()
    try:
        runs = json.loads(resp.body.decode("utf-8")).get("workflow_runs")
    except (ValueError, AttributeError):
        raise CheckUnavailable() from None
    runs = [r for r in runs or [] if isinstance(r, dict) and r.get("head_sha") == sha
            and r.get("event") == "push" and r.get("head_branch") == BRANCH
            and r.get("path") == f".github/workflows/{WORKFLOW}"]
    if not runs:
        return False
    newest = max(runs, key=lambda r: str(r.get("created_at") or ""))
    return newest.get("conclusion") == "success"


def newest_passing(repo: str, shas):
    """The first of `shas` (newest first) whose Tests run passed, or None."""
    for sha in list(shas)[:MAX_COMMITS]:
        if passed(repo, sha):
            return sha
    return None
