"""The `test` job is the one required check, so it must fail when any other job in tests.yml fails.

A job missing from its `needs` is invisible to it: a red `guards` once left `test` green.
Read as text because the CI test environments do not install a YAML parser.
Also pins the workflow's concurrency block, which nothing but GitHub evaluates."""
import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"


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


def _concurrency():
    text = WORKFLOW.read_text(encoding="utf-8")
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
