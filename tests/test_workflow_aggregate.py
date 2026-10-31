"""The `test` job is the one required check, so it must fail when any other job in tests.yml fails.

A job missing from its `needs` is invisible to it: a red `guards` once left `test` green."""
import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_every_other_job_gates_the_test_aggregate():
    jobs = _jobs()
    needs = set(jobs["test"]["needs"])
    assert set(jobs) - {"test"} == needs


def test_the_aggregate_refuses_a_failed_or_cancelled_job():
    step = _jobs()["test"]["steps"][0]
    env_jobs = set(re.findall(r"needs\.([\w-]+)\.result", " ".join(step["env"].values())))
    assert env_jobs == set(_jobs()["test"]["needs"])
    for var, expr in step["env"].items():
        if "needs.changes.result" in expr:
            assert f'"${var}" = success ] || exit 1' in step["run"]
        else:
            assert f'[ "${var}" = success ] || [ "${var}" = skipped ] || exit 1' in step["run"]
