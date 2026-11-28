"""The `test` job is the one required check, so it must fail when any other job in tests.yml fails.

A job missing from its `needs` is invisible to it: a red `guards` once left `test` green.
Read as text because the CI test environments do not install a YAML parser.
Also pins the concurrency blocks of tests.yml and windows-bootstrap.yml, which nothing but GitHub evaluates,
and the optional-tests job's file list."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "tests.yml"
BOOTSTRAP = WORKFLOWS / "windows-bootstrap.yml"


def _job_blocks():
    text = WORKFLOW.read_text(encoding="utf-8")
    jobs_part = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z][\w-]*):\n", jobs_part, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _aggregate_needs(block):
    listed = re.search(r"^    needs: \[([^\]]*)\]", block, re.M)
    return {n.strip() for n in listed.group(1).split(",")}


def test_every_other_job_gates_the_test_aggregate():
    blocks = _job_blocks()
    assert "test" in blocks
    assert set(blocks) - {"test"} == _aggregate_needs(blocks["test"])


def test_the_aggregate_refuses_a_failed_or_cancelled_job():
    block = _job_blocks()["test"]
    for job in _aggregate_needs(block):
        var = re.search(rf"^\s+(\w+): \$\{{\{{ needs\.{re.escape(job)}\.result \}}\}}", block, re.M)
        assert var, f"{job} result is not passed to the aggregate script"
        name = var.group(1)
        if job == "changes":
            assert f'[ "${name}" = success ] || exit 1' in block
        else:
            assert f'[ "${name}" = success ] || [ "${name}" = skipped ] || exit 1' in block


def _concurrency(path=WORKFLOW):
    text = path.read_text(encoding="utf-8")
    block = re.search(r"^concurrency:\n((?:  .*\n)+)", text, re.M).group(1)
    lines = [line.strip() for line in block.splitlines() if not line.strip().startswith("#")]
    return dict(line.split(": ", 1) for line in lines)


def test_pushes_to_the_base_share_one_group_that_never_cancels_a_running_run():
    """Merges that land back to back share a push run: every push to the base branch is in one group,
    where a newer push takes the place of a run still waiting but never stops the one that is going.

    Each part carries weight. A group per commit brings back a full run for every merge. Cancelling
    the run in progress leaves the base with no finished run while merges keep landing, and a dev copy
    moves only to a commit whose run passed. `queue: max` keeps every waiting run: a full run per merge
    again, each behind the last. Without the `push-` prefix the nightly run, which has the same ref and
    cancels what it supersedes, would share the group with the merges.

    Retire this when GitHub's merge queue tests each merge before it lands on the base branch."""
    block = _concurrency()
    assert block["group"] == ("${{ github.workflow }}-"
                              "${{ github.event_name == 'push' && 'push-' || '' }}${{ github.ref }}")
    assert block["cancel-in-progress"] == "${{ github.event_name != 'push' }}"
    assert set(block) == {"group", "cancel-in-progress"}


def test_the_windows_bootstrap_shares_its_pushes_the_same_way():
    """A group per commit gave every merge that touches the launcher its own run on a Windows runner, which
    bills at twice the Linux rate. It takes the group of tests.yml so a burst of merges costs one run going
    and one waiting; the block equals that one, whose parts are pinned above.

    Retire this when GitHub's merge queue tests each merge before it lands on the base branch."""
    assert _concurrency(BOOTSTRAP) == _concurrency(WORKFLOW)


def test_waiting_browser_tests_hold_the_merge_with_a_pending_status():
    """While the browser tests wait for auto-merge, the required `browser-tests` status must be pending:
    a skipped `e2e` job alone reads as passed, and a red run would email a failure that is not one.
    Success only when the tests passed or the change needs none; a cancelled run reports nothing."""
    block = _job_blocks()["browser-gate"]
    assert "if: ${{ !cancelled() && github.event_name == 'pull_request'" in block
    assert "statuses: write" in block and "actions/checkout" not in block
    assert "HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in block
    script = block.split("run: |", 1)[1]
    branches = re.findall(r'(?:if|elif) \[ "\$(\w+)" (!?=) (\w+) \]; then\n\s+state=(\w+)', script)
    assert branches == [("CHANGES", "!=", "success", "failure"), ("E2E_DEFERRED", "=", "true", "pending"),
                        ("E2E", "=", "success", "success"), ("E2E", "=", "skipped", "success")]
    assert re.search(r"else\n\s+state=failure", script)
    assert "context=browser-tests" in script and '[ "$state" != failure ]' in script
    # A read-only token (a fork, Dependabot) must leave the status missing, not turn `test` red.
    assert re.search(r'>/dev/null \\\n\s+\|\| echo "::warning::', script)


def test_turning_on_auto_merge_starts_the_run_with_the_browser_tests():
    text = WORKFLOW.read_text(encoding="utf-8")
    types = re.search(r"^  pull_request:\n(?:    .*\n)*?    types: \[([^\]]*)\]", text, re.M).group(1)
    assert "auto_merge_enabled" in {t.strip() for t in types.split(",")}
    assert ('--merge-approved "$MERGE_APPROVED"' in text
            and "MERGE_APPROVED: ${{ github.event.pull_request.auto_merge != null"
                " || github.event.action == 'auto_merge_enabled' }}" in text)


# pip names on the optional-tests install line whose import name differs.
_IMPORT_NAMES = {"opencv-contrib-python-headless": "cv2", "pyyaml": "yaml",
                 "opencc-python-reimplemented": "opencc", "audioop-lts": "audioop"}


def _skips_whole_file_without(path):
    """The modules a test file needs at import: a module-level `pytest.importorskip("x")`."""
    needed = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        call = getattr(node, "value", None)
        if (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "importorskip"
                and call.args and isinstance(call.args[0], ast.Constant)):
            needed.add(call.args[0].value.split(".")[0])
    return needed


def test_every_file_that_needs_an_optional_library_runs_in_the_optional_job():
    """Other shards don't install these libraries, so a test file that skips at import without one runs nowhere else;
    six comic test files once went unrun for that reason.

    Retire this when the job's file list is generated instead of written by hand."""
    block = _job_blocks()["optional-tests"]
    install = re.search(r"run: pip install (.*)", block).group(1).split()
    extras = {_IMPORT_NAMES.get(w, w) for i, w in enumerate(install)
              if not w.startswith("-") and install[i - 1] not in ("-r", "-c")}
    listed = set(re.findall(r"tests/test_\w+\.py", block))
    missing = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")
                     if _skips_whole_file_without(p) & extras
                     and p.relative_to(ROOT).as_posix() not in listed)
    assert missing == []


def test_a_run_that_reuses_on_approval_records_what_the_tree_needed():
    """The push after the merge shortens its run from this record; the reused run covered the rest."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "tools/ci_tested_tree.py --on-approval) || out=$needed" in text
    assert 'record=$needed || record=$out' in text
    assert 'printf \'%s\\n\' "$record" | python3 tools/ci_tested_tree.py --write tested.json' in text
