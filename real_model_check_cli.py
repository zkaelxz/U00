"""
real_model_check_cli.py -- the `smoke` command: the Diagnostics page's
real-model check from a terminal. It calls the same
real_model_check_service.run_checks as the app's job, so the two cannot
report different checks; this module only adds what a terminal needs
(arguments, the cross-process GPU slot, plain text, the exit code).

  python cli.py smoke
  python cli.py smoke --speech-clip hello.wav --expected-text "你好"

Exit code 1 when any check fails; skipped and could-not-check results are
reported as such and do not fail the run. Like the API, the output never
carries keys, filesystem paths or URLs.
"""

import os
import sys

import diagnostics
import translate_engines
from jobs import gpu_slots
from services import real_model_check_service as svc

USAGE_ERROR, GPU_BUSY = 2, 2

_WORDS = {svc.PASS: "PASS", svc.FAIL: "FAIL", svc.SKIPPED: "SKIPPED",
          svc.COULD_NOT_CHECK: "COULD NOT CHECK"}


def register(sub) -> None:
    p = sub.add_parser("smoke", help="Run a tiny transcription, OCR read and Ollama translation "
                                     "with the models you have (nothing is downloaded)")
    p.add_argument("--speech-clip", default=None, metavar="FILE",
                   help="Also transcribe this short speech clip")
    p.add_argument("--expected-text", default=None,
                   help="With --speech-clip: fail unless the transcript contains this text")
    p.set_defaults(func=cmd_smoke)


def _safe(text) -> str:
    # redact_for_support collapses paths and the username; every error text
    # also takes the secret pass, whatever else it contains.
    return translate_engines.redact_secrets(diagnostics.redact_for_support(str(text)))


def format_report(results: list) -> str:
    lines = [f"[{_WORDS[r['status']]}] {r['label']}: {_safe(r['reason'])}" for r in results]
    counts = [f"{sum(r['status'] == status for r in results)} {word.lower()}"
              for status, word in _WORDS.items()]
    return "\n".join(lines + ["", ", ".join(counts)])


def exit_code(results: list) -> int:
    return 1 if any(r["status"] == svc.FAIL for r in results) else 0


def run(speech_clip=None, expected_text=None, out=print) -> int:
    if expected_text and not speech_clip:
        out("--expected-text needs --speech-clip.")
        return USAGE_ERROR
    holder = f"check:{os.getpid()}"
    try:
        # The CLI never starts background jobs, so it claims the same
        # cross-process slot the app's GPU jobs use.
        if not gpu_slots.acquire(holder, "Real-model check"):
            out(f"The GPU is busy with: {_safe(gpu_slots.status()[1] or 'another job')}. "
                "Try again when it ends.")
            return GPU_BUSY
    except Exception as exc:
        out(f"The real-model check could not start: {_safe(exc) or type(exc).__name__}")
        return 1
    try:
        out("Running the real-model check; this can take a minute.")
        results = svc.run_checks(speech_clip=speech_clip, expected_text=expected_text)
    except Exception as exc:
        out(f"The real-model check stopped: {_safe(exc) or type(exc).__name__}")
        return 1
    finally:
        gpu_slots.release(holder)
    out(format_report(results))
    return exit_code(results)


def cmd_smoke(args) -> None:
    code = run(args.speech_clip, args.expected_text)
    if code:
        sys.exit(code)
