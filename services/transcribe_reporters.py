"""
services/transcribe_reporters.py -- how the transcription pipeline reports
progress, stages and cancellation: directly to the job record inside a
thread job, through the result queue inside the worker process.
"""
from jobs import process_worker
import background_jobs


class _ThreadReporter:
    """Progress, stages and cancel for the pipeline running inside a thread
    job, which owns the job record directly."""

    def __init__(self, job_id):
        self.job_id = job_id

    def progress(self, frac, message=""):
        background_jobs.update_progress(self.job_id, frac, message)

    def stage(self, message, frac=0.0):
        return background_jobs.stage_ticker(self.job_id, message, frac=frac)

    def cancelled(self) -> bool:
        return background_jobs.is_cancel_requested(self.job_id)

    def checkpoint_unit(self, scope, unit_id, payload):
        """Only a process job's run is resumable; a thread job's has no checkpoint."""

    def raise_if_cancelled(self):
        if self.cancelled():
            raise background_jobs.JobCancelled(self.job_id)


class _ProcessStage:
    """stage_ticker's start/stop/with shape inside the worker process: the
    parent's watcher runs the real ticker (report_stage)."""

    def __init__(self, result_queue, message, frac):
        self._queue, self._message, self._frac = result_queue, message, frac
        self._open = False

    def start(self):
        if not self._open:
            self._open = True
            process_worker.report_stage(self._queue, self._message, self._frac)
        return self

    def stop(self):
        """Idempotent."""
        if self._open:
            self._open = False
            process_worker.report_stage(self._queue, "", self._frac)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


class _ProcessReporter:
    """The same inside the worker process: progress and stages go to the
    parent through the result queue. A cancel kills the process, so there is
    nothing to check here; the parent re-checks before applying anything.
    Every report and stage-boundary check ends the worker if its parent has
    died (exit_if_parent_gone)."""

    job_id = None

    def __init__(self, result_queue):
        self._queue = result_queue

    def progress(self, frac, message=""):
        process_worker.report_progress(self._queue, frac, message)

    def stage(self, message, frac=0.0):
        return _ProcessStage(self._queue, message, frac)

    def checkpoint_unit(self, scope, unit_id, payload):
        process_worker.report_checkpoint_unit(self._queue, scope, unit_id, payload)

    def cancelled(self) -> bool:
        process_worker.exit_if_parent_gone()
        return False

    def raise_if_cancelled(self):
        process_worker.exit_if_parent_gone()
