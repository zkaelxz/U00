"""
ui/status.py -- the shared background-job status block (Step 13 item 1).

`workspace_tab.py` currently repeats the same status-rendering shape at
every one of its job call sites (transcription, translation, speaker
detection, consistency check, flagging, emotion detection, notes,
re-segmentation, dub generation, ...):

    if job["status"] == "running":
        st.progress(job["progress"], text=(job.get("message") or "...") +
                    background_jobs.eta_text(job))
    elif job["status"] == "error":
        st.error(f"... failed: {job['error']}")
        with st.expander("Details"):
            st.code(job.get("traceback", ""), language="text")

Step 12 item 6 already asked for this to be pulled into one shared
helper; this is that helper, built once. Not wired into workspace_tab.py
or any other tab by this step -- Step 12/14 and later steps switch their
own call sites over as they're touched.

Reads a job dict shaped like `background_jobs.get_status(job_id)`'s
return value: {"status", "progress", "message", "error", "traceback",
...}. Doesn't import background_jobs at call time for anything other than
`eta_text`, and degrades quietly if that's unavailable (e.g. a caller
passes a fake dict in a test with no "started_at").

Also covers the failure case, not as a separate component but as this
same block's failure state: a friendly error card with a plain-language
reason up top, a [Retry] button where the caller says retrying is safe,
and the raw exception/traceback behind a collapsed "Advanced details"
section rather than shown by default.
"""


def render_job_status(job: dict | None, *, running_label: str = "Working...",
                       done_message: str | None = None, reason: str | None = None,
                       on_retry=None, retry_label: str = "Retry", key_prefix: str = "status"):
    """Renders one job's current state: nothing if there's no job yet or
    it's already been cleared, a progress bar (with ETA where available)
    while running or queued, a success message once done (only if
    `done_message` is given -- many callers show their own follow-up UI
    instead), or the failure card on error.

    reason/on_retry/retry_label/key_prefix are passed straight through to
    render_failure_card() for the error case; see there.
    """
    import streamlit as st
    if not job:
        return
    status = job.get("status")
    if status == "queued":
        st.info(job.get("message") or "Waiting...")
    elif status == "running":
        message = (job.get("message") or running_label) + _eta_suffix(job)
        st.progress(job.get("progress") or 0.0, text=message)
    elif status == "error":
        render_failure_card(job, reason=reason, on_retry=on_retry,
                            retry_label=retry_label, key_prefix=key_prefix)
    elif status == "done" and done_message:
        st.success(done_message)


def render_failure_card(job: dict, *, reason: str | None = None, on_retry=None,
                        retry_label: str = "Retry", key_prefix: str = "status"):
    """The friendly-failure-card state on its own, for a caller that
    already knows it has an error job and wants just the card (e.g. a
    step whose done/running rendering differs from render_job_status's
    defaults).

    reason: a plain-language explanation to show as the headline. Falls
        back to a best-effort de-technicalized version of job["error"]
        (stripping a leading "ExceptionType: ") when not given.
    on_retry: a zero-argument callable to run if the [Retry] button is
        clicked. Omit when retrying this particular failure isn't safe
        (e.g. it could resubmit a partially-applied write) -- no button
        is shown in that case.
    """
    import streamlit as st
    st.error(reason or _default_reason(job))
    if on_retry is not None:
        if st.button(retry_label, key=f"{key_prefix}_retry"):
            on_retry()
    with st.expander("Advanced details", expanded=False):
        st.code(job.get("traceback") or job.get("error") or "No further details available.",
                language="text")


def _default_reason(job: dict) -> str:
    """Best-effort plain-language fallback when the caller doesn't supply
    its own `reason`: strips a leading "ExceptionType: " off the stored
    error message, since "Something went wrong: ConnectionError: ..." at
    least reads as a sentence rather than a Python exception signature.
    Callers that know the failure well should still pass their own
    `reason` -- this is only a fallback, not real error humanization."""
    err = (job.get("error") or "").strip()
    if not err:
        return "Something went wrong."
    _, sep, rest = err.partition(":")
    return rest.strip() if sep and rest.strip() else err


def _eta_suffix(job: dict) -> str:
    try:
        import background_jobs
        return background_jobs.eta_text(job)
    except Exception:
        return ""
