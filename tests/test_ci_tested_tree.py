"""tools/ci_tested_tree.py: a push to the base skips only what a green PR run of the identical tree ran.
A wrong skip lets a regression through, so every doubt must leave the push's outputs as they were.
The GitHub API is faked; no network."""
import io
import json
import os
import sys
import urllib.error
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import ci_changes  # noqa: E402
import ci_tested_tree as ctt  # noqa: E402

REPO, SHA, HEAD, TREE, BASE = "o/r", "m" * 40, "h" * 40, "t" * 40, "b" * 40
ALL = ci_changes._all_true()


def lines(outputs):
    return "".join(f"{k}={str(outputs[k]).lower()}\n" for k in ci_changes.OUTPUTS)


def pr_outputs(**over):
    return {**ALL, **over}


def zipped(record):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("tested.json", json.dumps(record))
    return buf.getvalue()


def trees(head=TREE, base=BASE):
    return lambda ref="HEAD": {"HEAD": head, "HEAD^1": base}[ref]


def green_run(**over):
    # GitHub empties `pull_requests` once the PR closes, which it has by the time the push runs.
    return {"id": 7, "run_number": 3, "status": "completed", "conclusion": "success",
            "head_repository": {"full_name": REPO}, "pull_requests": [], **over}


def record_of(tested, tree=TREE, base_tree=BASE):
    return {"tree": tree, "base_tree": base_tree, "outputs": {k: str(v).lower() for k, v in tested.items()}}


def fake_github(tested, tree=TREE, run=None, pulls=None, base_tree=BASE, runs=None):
    """`runs` is [(run, record or None)], any order; by default one green run of `tested`."""
    runs = runs if runs is not None else [(run or green_run(), record_of(tested, tree, base_tree))]
    records = {r["id"]: rec for r, rec in runs}
    asked = []

    def get(url, token, raw=False):
        asked.append(url)
        assert token == "tok"
        if url.endswith(f"/commits/{SHA}/pulls"):
            return pulls if pulls is not None else [
                {"number": 41, "merge_commit_sha": SHA, "merged_at": "2026-11-14T09:00:00Z", "head": {"sha": HEAD},
                 "base": {"ref": "baihe-subtitler"}}]
        if "/actions/workflows/tests.yml/runs" in url:
            assert f"head_sha={HEAD}" in url and "event=pull_request" in url
            return {"workflow_runs": [r for r, _ in runs]}
        for run_id, rec in records.items():
            if url.endswith(f"/actions/runs/{run_id}/artifacts?name=tested-tree"):
                return {"artifacts": [] if rec is None else [
                    {"expired": False, "archive_download_url": f"https://dl/{run_id}"}]}
            if url == f"https://dl/{run_id}":
                assert raw
                return zipped(rec)
        raise AssertionError(url)
    return get, asked


def after_merge(get, merged=("services/x.py",), stdin=None, tree=None):
    out = io.StringIO()
    ctt.main(["--after-merge"], stdin=io.StringIO(stdin if stdin is not None else lines(ALL)), stdout=out,
             env={"GITHUB_REPOSITORY": REPO, "GITHUB_SHA": SHA, "GITHUB_TOKEN": "tok"},
             tree=tree or trees(), get=get, merged_files=lambda: list(merged))
    return ctt.parse_outputs(out.getvalue())


class TestAfterTested:
    def test_a_full_pr_run_leaves_only_the_always_on_jobs(self):
        out = ctt.after_tested(ALL, ALL)
        assert out == {**ALL, "run_e2e": False, "run_full_pytest": False, "run_windows_scripts": False,
                       "run_caddy": False, "run_app_checks": False, "run_sources_subset": False}
        assert out["notes_only"] is False  # test-shard still runs the frontend-reading tests

    def test_groups_the_pr_skipped_still_run(self):
        tested = pr_outputs(run_e2e=False, run_windows_scripts=False, run_caddy=False)
        out = ctt.after_tested(ALL, tested)
        assert out["run_e2e"] and out["run_windows_scripts"] and out["run_caddy"]
        assert not out["run_full_pytest"] and not out["run_app_checks"]

    def test_a_sources_subset_or_frontend_only_pr_does_not_count_as_the_full_suite(self):
        assert ctt.after_tested(ALL, pr_outputs(run_sources_subset=True))["run_full_pytest"] is True
        assert ctt.after_tested(ALL, pr_outputs(run_full_pytest=False))["run_full_pytest"] is True

    def test_a_notes_only_pr_ran_nothing_to_skip(self):
        assert ctt.after_tested(ALL, pr_outputs(notes_only=True)) == ALL


class TestAfterMerge:
    def test_a_green_run_of_the_same_tree_skips_what_it_ran(self):
        get, _ = fake_github(pr_outputs(run_e2e=False))
        out = after_merge(get)
        assert out["run_e2e"] is True and out["run_full_pytest"] is False and out["run_app_checks"] is False

    def test_a_different_tree_runs_everything(self):
        get, _ = fake_github(ALL, tree="x" * 40)
        assert after_merge(get) == ALL

    def test_a_run_from_a_fork_does_not_count(self):
        get, _ = fake_github(ALL, run=green_run(head_repository={"full_name": "someone/r"}))
        assert after_merge(get) == ALL

    def test_a_run_recorded_on_another_base_runs_everything(self):
        # A run of the same head into another branch, or before the base moved: its base tree differs.
        get, _ = fake_github(ALL, base_tree="x" * 40)
        assert after_merge(get) == ALL

    def test_which_pr_a_run_lists_does_not_matter_once_the_trees_match(self):
        prs = [{"number": 99, "base": {"ref": "baihe-subtitler"}}]
        get, _ = fake_github(pr_outputs(run_e2e=False), run=green_run(pull_requests=prs))
        assert after_merge(get)["run_full_pytest"] is False

    @pytest.mark.parametrize("newer", [
        green_run(id=8, run_number=4, conclusion="failure"),  # a later flaky or failed run of the same code
        green_run(id=8, run_number=4, status="in_progress", conclusion=None),  # a re-run still going
        green_run(id=8, run_number=4, head_repository={"full_name": "someone/r"}),  # a fork's green word
    ])
    def test_the_newest_run_of_the_tree_decides(self, newer):
        get, _ = fake_github(ALL, runs=[(green_run(), record_of(ALL)), (newer, record_of(ALL))])
        assert after_merge(get) == ALL

    def test_a_newer_run_of_another_tree_or_without_a_record_leaves_the_green_one(self):
        runs = [(green_run(), record_of(pr_outputs(run_e2e=False))),
                (green_run(id=8, run_number=4, conclusion="failure"), record_of(ALL, tree="x" * 40)),
                (green_run(id=9, run_number=5, conclusion="cancelled"), None)]
        get, _ = fake_github(ALL, runs=runs)
        out = after_merge(get)
        assert out["run_e2e"] is True and out["run_full_pytest"] is False

    def test_a_pr_merged_into_another_branch_does_not_count(self):
        get, _ = fake_github(ALL, pulls=[{"number": 41, "merge_commit_sha": SHA, "merged_at": "x",
                                          "head": {"sha": HEAD}, "base": {"ref": "other"}}])
        assert after_merge(get) == ALL

    def test_a_merge_onto_another_base_than_the_run_tested_runs_everything(self):
        # The same tree on another parent: a rebase merge of several commits, or a base that moved and back.
        get, _ = fake_github(ALL)
        assert after_merge(get, tree=trees(base="x" * 40)) == ALL

    def test_a_push_that_is_no_pr_merge_runs_everything(self):
        get, _ = fake_github(ALL, pulls=[{"number": 41, "merge_commit_sha": "z" * 40, "merged_at": "x",
                                          "head": {"sha": HEAD}, "base": {"ref": "baihe-subtitler"}}])
        assert after_merge(get) == ALL

    @pytest.mark.parametrize("merged", [[".github/workflows/tests.yml"], ["tools/ci_tested_tree.py"],
                                        ["services/x.py", "requirements-core.txt"]])
    def test_a_merge_that_changes_ci_or_dependency_files_runs_everything_without_asking(self, merged):
        get, asked = fake_github(ALL)
        assert after_merge(get, merged=merged) == ALL
        assert asked == []

    def test_an_api_failure_runs_everything(self):
        def get(url, token, raw=False):
            raise urllib.error.URLError("down")
        assert after_merge(get) == ALL

    def test_a_malformed_record_runs_everything(self):
        get, _ = fake_github(ALL)

        def broken(url, token, raw=False):
            return b"not a zip" if raw else get(url, token, raw)
        assert after_merge(broken) == ALL

    def test_only_the_push_outputs_it_was_given_are_reduced(self):
        # A push the classifier already narrowed stays at least that narrow, never wider.
        push = pr_outputs(run_caddy=False)
        get, _ = fake_github(pr_outputs(run_e2e=False))
        out = after_merge(get, stdin=lines(push))
        assert out["run_caddy"] is False and out["run_e2e"] is True

    def test_unreadable_push_outputs_are_passed_through_for_the_workflow_to_reject(self):
        out = io.StringIO()
        ctt.main(["--after-merge"], stdin=io.StringIO("garbage\n"), stdout=out, env={})
        assert out.getvalue() == "garbage\n"


class TestWrite:
    def test_records_the_tree_and_the_outputs(self, tmp_path):
        path = tmp_path / "tested.json"
        ctt.main(["--write", str(path)], stdin=io.StringIO(lines(ALL)), tree=trees())
        record = json.loads(path.read_text())
        assert record["tree"] == TREE and record["base_tree"] == BASE
        assert ctt.parse_outputs("\n".join(f"{k}={v}" for k, v in record["outputs"].items())) == ALL

    def test_never_fails_the_pr_run(self, tmp_path):
        path = tmp_path / "tested.json"

        def no_git(ref="HEAD"):
            raise OSError("no git")
        assert ctt.main(["--write", str(path)], stdin=io.StringIO(lines(ALL)), tree=no_git) == 0
        assert not path.exists()


def test_the_artifact_redirect_is_followed_without_the_token(monkeypatch):
    seen = {}

    class Opener:
        def open(self, req, timeout):
            seen["first"] = dict(req.header_items())
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "https://blob/signed"}, None)

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(url, timeout):
        seen["second"] = url
        return Resp(b"zip-bytes")
    monkeypatch.setattr(ctt.urllib.request, "build_opener", lambda *h: Opener())
    monkeypatch.setattr(ctt.urllib.request, "urlopen", urlopen)
    assert ctt._get("https://api.github.com/x/zip", "tok", raw=True) == b"zip-bytes"
    assert seen["first"]["Authorization"] == "Bearer tok"
    assert seen["second"] == "https://blob/signed"  # a bare URL: no Authorization header goes with it
