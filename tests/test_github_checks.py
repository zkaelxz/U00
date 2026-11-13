"""lib/github_checks.py and tools/update_target.py: a dev copy moves only to a
commit whose Tests run passed. lib.http is faked; no network."""

import json

import pytest

from lib import github_checks as gc
from lib import http

A, B, C = "a" * 40, "b" * 40, "c" * 40


def _runs(*runs):
    return http.Response(200, {}, json.dumps({"workflow_runs": list(runs)}).encode(), "u")


def _run(conclusion, created="2026-11-12T10:00:00Z", sha=A, event="push",
         branch="baihe-subtitler", path=".github/workflows/tests.yml"):
    return {"conclusion": conclusion, "created_at": created, "head_sha": sha, "event": event,
            "head_branch": branch, "path": path}


@pytest.fixture
def github(monkeypatch):
    answers, asked = {}, []

    def get(url, **kw):
        asked.append((url, kw))
        answer = answers.get(kw["params"]["head_sha"], _runs())
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(gc.http, "get", get)
    return answers, asked


def test_a_passed_push_run_counts(github):
    answers, asked = github
    answers[A] = _runs(_run("success"))
    assert gc.passed("o/r", A) is True
    url, kw = asked[0]
    assert url == "https://api.github.com/repos/o/r/actions/workflows/tests.yml/runs"
    assert kw["params"] == {"head_sha": A, "event": "push", "branch": "baihe-subtitler",
                            "per_page": 100}
    assert kw["timeout"] and kw["guard"] is None


@pytest.mark.parametrize("runs", [
    [], [_run("failure")], [_run(None)],
    # A pull request's run tests a merge with the base, not this commit.
    [_run("success", event="pull_request")],
    [_run("success", branch="someone-elses-branch")],
    [_run("success", path=".github/workflows/windows-installer.yml")],
    [_run("success", sha=B)],
    # A newer run that failed after an earlier pass: the newest run counts.
    [_run("success", "2026-11-12T10:00:00Z"), _run("failure", "2026-11-12T11:00:00Z")],
])
def test_anything_but_a_newest_passed_push_run_does_not_count(github, runs):
    answers, _ = github
    answers[A] = _runs(*runs)
    assert gc.passed("o/r", A) is False


@pytest.mark.parametrize("answer", [
    http.FetchError(), http.Response(403, {}, b"rate limited", "u"),
    http.Response(200, {}, b"not json", "u")])
def test_an_unusable_answer_is_unavailable_not_red(github, answer):
    answers, _ = github
    answers[A] = answer
    with pytest.raises(gc.CheckUnavailable):
        gc.passed("o/r", A)


def test_the_newest_green_commit_wins_and_older_ones_are_not_asked(github):
    answers, asked = github
    answers[B] = _runs(_run("success", sha=B))
    answers[C] = _runs(_run("success", sha=C))
    assert gc.newest_passing("o/r", [A, B, C]) == B
    assert len(asked) == 2


def test_no_green_commit_is_none(github):
    assert gc.newest_passing("o/r", [A, B]) is None


@pytest.mark.parametrize("bad", ["../x", "o/r/../../y", "o"])
def test_odd_repos_or_shas_are_never_asked(github, bad):
    _, asked = github
    with pytest.raises(gc.CheckUnavailable):
        gc.passed(bad, A)
    with pytest.raises(gc.CheckUnavailable):
        gc.passed("o/r", "HEAD")
    assert asked == []


@pytest.mark.parametrize("url,repo", [
    ("https://github.com/zkaelxz/U00.git", "zkaelxz/U00"),
    ("https://github.com/zkaelxz/U00", "zkaelxz/U00"),
    ("git@github.com:zkaelxz/U00.git", "zkaelxz/U00"),
    ("https://gitlab.com/a/b.git", None), ("", None)])
def test_repo_from_remote(url, repo):
    assert gc.repo_from_remote(url) == repo


def test_update_target_prints_what_start_bat_reads(monkeypatch, capsys):
    from services import source_update_service as su
    from tools import update_target
    monkeypatch.setattr(su, "update_target", lambda old, new: (B, None))
    assert update_target.main([A, C]) == 0
    assert capsys.readouterr().out == f"ok {B}\n"
    monkeypatch.setattr(su, "update_target", lambda old, new: (None, su.NO_GREEN))
    assert update_target.main([A, C]) == 1
    assert capsys.readouterr().out == f"no {su.NO_GREEN}\n"


def test_update_target_never_fails_the_launch(monkeypatch, capsys):
    from services import source_update_service as su
    from tools import update_target

    def broken(old, new):
        raise RuntimeError("anything")
    monkeypatch.setattr(su, "update_target", broken)
    assert update_target.main([A, C]) == 1
    assert capsys.readouterr().out.startswith("no ")
