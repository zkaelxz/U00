"""
cli_timing.py -- `python cli.py timing-check`: the headless twin of Review's
"Check timing" and "Snap all flagged", through the same
services/timing_check_service.py. Also waits for the check a Qwen-only
`transcribe` starts by itself, so the process doesn't exit under it.
Kept out of cli.py, which has no room to grow.

  python cli.py timing-check --id 25
  python cli.py timing-check --id 25 --snap     # then snap every flagged line (history snapshot first)
"""

import sys

import background_jobs
import translate_engines
from services import timing_check_service
from services.service_errors import ServiceError


def _report(label: str, result: dict) -> None:
    if result.get("notice"):
        print(f"{label}: {translate_engines.redact_secrets(result['notice'])}")
        return
    print(f"{label}: checked {result['checked']} line(s): {result['flagged']} flagged, "
          f"{result['cleared']} cleared, {result['skipped_flagged']} left with their existing flag.")


def wait_after_transcribe(title_id: int, label: str, wait) -> None:
    """`wait` is cli._wait_for_job. A failed check is a warning: the transcript is already saved."""
    job_id = timing_check_service.job_id_for(title_id)
    if not background_jobs.is_running(job_id):
        return
    outcome, message, result = wait(job_id, f"{label} timing")
    if outcome in ("ok", "partial"):
        _report(f"{label} timing check", result)
    else:
        print(f"{label} timing check {outcome}: {translate_engines.redact_secrets(message or '')}",
              file=sys.stderr)


def cmd_timing_check(args, wait) -> None:
    label = f"#{args.id}"
    try:
        job = timing_check_service.start_timing_check(args.id)
    except ServiceError as e:
        raise SystemExit(f"timing-check: {translate_engines.redact_secrets(e.message)}")
    outcome, message, result = wait(job["job_id"], label)
    if outcome not in ("ok", "partial"):
        print(f"{label} timing check {outcome}: {translate_engines.redact_secrets(message or '')}",
              file=sys.stderr)
        sys.exit(1)
    _report(label, result)
    if args.snap:
        snapped = timing_check_service.snap_to_speech(args.id)
        print(f"{label}: snapped {snapped['snapped']} line(s) to speech, "
              f"{len(snapped['stale_ids'])} changed since the check and were left alone.")


def register(sub, wait) -> None:
    """`wait` is cli._wait_for_job."""
    p = sub.add_parser("timing-check", help="Flag lines whose timing disagrees with the speech in the audio")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--snap", action="store_true", help="Then snap every flagged line to the speech.")
    p.set_defaults(func=lambda args: cmd_timing_check(args, wait))
