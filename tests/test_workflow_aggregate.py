"""The `test` job is the one required check, so it must fail when any other job in tests.yml fails.

A job missing from its `needs` is invisible to it: a red `guards` once left `test` green.
Read as text because the CI test environments do not install a YAML parser."""
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
