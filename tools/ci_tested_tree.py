"""After a merge to the base branch, which CI jobs a green PR run of the identical tree already ran.

    python tools/ci_tested_tree.py --write tested.json < outputs      (PR run: record what it tests)
    python tools/ci_tested_tree.py --after-merge < outputs           (push run: drop what was run)

`outputs` is the `name=true|false` lines tools/ci_changes.py printed. A squash merge of a PR whose
last green run tested the same merge tree adds nothing that run did not test, so the push run keeps
only the groups that run skipped. Every lookup failure prints the push run's outputs unchanged, so a
broken lookup may cost minutes, never skip a job. A merge that changes CI or dependency files
(ci_changes' "everything" files) always runs everything, since the PR run used its own copy of the
workflow; so does a run from a fork. A nightly scheduled run still runs everything.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

import ci_changes

ARTIFACT = "tested-tree"
_FILE = "tested.json"
_API = "https://api.github.com"
_TIMEOUT = 10
_MAX_ARTIFACT_BYTES = 64 * 1024
# Groups a PR run's outputs say it ran, and the push output each one lets go.
_SKIPPABLE = ("run_e2e", "run_windows_scripts", "run_caddy", "run_app_checks")


def parse_outputs(text):
    out = {}
    for line in text.splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name in ci_changes.OUTPUTS and value in ("true", "false"):
            out[name] = value == "true"
    if set(out) != set(ci_changes.OUTPUTS):
        raise ValueError("incomplete outputs")
    return out


def print_outputs(outputs, stream=sys.stdout):
    for name in ci_changes.OUTPUTS:
        stream.write(f"{name}={str(outputs[name]).lower()}\n")


def after_tested(push, tested):
    """The push run's outputs minus what `tested` (a green PR run's outputs) already ran.
    `guards` and the `test` gate always run; notes_only stays false so `test-shard` runs at
    least the tests that read frontend files."""
    out = dict(push)
    if tested["notes_only"]:
        return out
    if tested["run_full_pytest"] and not tested["run_sources_subset"]:
        out["run_full_pytest"] = False
        out["run_sources_subset"] = False
    for name in _SKIPPABLE:
        if tested[name]:
            out[name] = False
    return out


def _merged_files():
    return subprocess.run(["git", "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD"], check=True,
                          capture_output=True, text=True, encoding="utf-8", timeout=_TIMEOUT).stdout.splitlines()


def _tree(ref="HEAD"):
    return subprocess.run(["git", "rev-parse", f"{ref}^{{tree}}"], check=True, capture_output=True,
                          text=True, encoding="utf-8", timeout=_TIMEOUT).stdout.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get(url, token, raw=False):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=_TIMEOUT) as resp:
            body = resp.read(_MAX_ARTIFACT_BYTES + 1 if raw else None)
    except urllib.error.HTTPError as exc:
        # An artifact download answers with a redirect to signed storage, which must not get the token.
        if raw and exc.code in (301, 302, 303, 307) and exc.headers.get("Location"):
            with urllib.request.urlopen(exc.headers["Location"], timeout=_TIMEOUT) as resp:
                body = resp.read(_MAX_ARTIFACT_BYTES + 1)
        else:
            raise
    if raw:
        if len(body) > _MAX_ARTIFACT_BYTES:
            raise ValueError("artifact too large")
        return body
    return json.loads(body)


def find_tested(repo, sha, tree, token, get=_get):
    """The outputs of a green Tests run of the PR merged as `sha` whose tested tree is `tree`, or None."""
    pulls = get(f"{_API}/repos/{repo}/commits/{sha}/pulls", token)
    heads = [p["head"]["sha"] for p in pulls if p.get("merge_commit_sha") == sha and p.get("merged_at")]
    for head in heads:
        runs = get(f"{_API}/repos/{repo}/actions/workflows/tests.yml/runs"
                   f"?event=pull_request&status=success&head_sha={head}&per_page=20", token)
        for run in sorted(runs.get("workflow_runs", []), key=lambda r: r.get("run_number", 0), reverse=True):
            if run.get("conclusion") != "success" or (run.get("head_repository") or {}).get("full_name") != repo:
                continue
            arts = get(f"{_API}/repos/{repo}/actions/runs/{run['id']}/artifacts?name={ARTIFACT}", token)
            for art in arts.get("artifacts", []):
                if art.get("expired"):
                    continue
                blob = get(art["archive_download_url"], token, raw=True)
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    record = json.loads(z.read(_FILE))
                if record.get("tree") == tree:
                    return parse_outputs("\n".join(f"{k}={v}" for k, v in record["outputs"].items()))
    return None


def main(argv=None, stdin=None, stdout=None, env=None, tree=_tree, get=_get, merged_files=_merged_files):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", metavar="PATH")
    parser.add_argument("--after-merge", action="store_true")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    stdout = stdout or sys.stdout
    text = (stdin or sys.stdin).read()
    if args.write:
        # Never fails the PR run: without the record the push after it simply runs everything.
        try:
            record = {"tree": tree(), "outputs": {k: str(v).lower() for k, v in parse_outputs(text).items()}}
            with open(args.write, "w", encoding="utf-8") as f:
                json.dump(record, f)
        except Exception as exc:
            print(f"ci_tested_tree: not recorded: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    try:
        push = parse_outputs(text)
    except ValueError:
        stdout.write(text)
        return 0
    try:
        if any(ci_changes._forces_everything(f.strip()) for f in merged_files() if f.strip()):
            raise ValueError("the merge changes CI or dependency files")
        tested = find_tested(env["GITHUB_REPOSITORY"], env["GITHUB_SHA"], tree(), env["GITHUB_TOKEN"], get)
    except Exception as exc:
        print(f"ci_tested_tree: {type(exc).__name__}: {exc}; running everything the push needs", file=sys.stderr)
        tested = None
    if tested is None:
        print("ci_tested_tree: no green PR run of this tree; running everything the push needs", file=sys.stderr)
        print_outputs(push, stdout)
    else:
        print("ci_tested_tree: a green PR run tested this tree; running only what it skipped", file=sys.stderr)
        print_outputs(after_tested(push, tested), stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
