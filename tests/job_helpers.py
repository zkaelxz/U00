"""tests/job_helpers.py -- shared polling for background-job tests."""
import time

import background_jobs


def wait_for_job(job_id, timeout=10.0, missing_ok=False):
    """Block until the job has left running/queued, then return its status.

    Background jobs run on threads, so a test must wait here before asserting
    on their results. With `missing_ok`, a job that is gone (cleared by
    cancel or by the code under test) counts as finished and returns None.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = background_jobs.get_status(job_id)
        if not job and missing_ok:
            return None
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")
