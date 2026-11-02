"""voice_worker.py -- the child that speaks a batch of lines inside a voice engine's own venv.

voice_venv.run_batch starts it as `<venv python> -I -X utf8 voice_worker.py <spec file>`.
It runs under a different interpreter whose packages clash with the app's (qwen-tts pins
an older transformers), so it imports nothing from the app and only the standard library
at module level; an engine's packages are imported by its adapter once the spec names it.

The model is loaded once per batch, then each item is written to its "out" path. Events
go to stdout as `@@voice-<nonce> {json}` lines, one per event; anything else on the stream
is library noise and the parent ignores it. Events: {"loaded": true} once the model is up,
{"done": id} or {"failed": id, "error": text} per item, {"fatal": text} when nothing more
can be spoken.
"""

import json
import sys

PREFIX = "@@voice-"
ERROR_MAX_CHARS = 300

# engine -> load(spec) returning speak(item), which writes item["out"]. Filled in by the
# engines that run in a venv of their own.
ADAPTERS = {}


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:ERROR_MAX_CHARS]


def run(spec: dict, emit, adapters=None) -> None:
    """Speaks every item in `spec["items"]`, reporting through `emit(event)`. One item
    failing does not stop the rest; a model that can't load stops the batch."""
    load = (ADAPTERS if adapters is None else adapters).get(spec.get("engine"))
    if load is None:
        emit({"fatal": "Unknown voice engine."})
        return
    try:
        speak = load(spec)
    except Exception as exc:
        emit({"fatal": _error_text(exc)})
        return
    emit({"loaded": True})
    for item in spec.get("items") or []:
        try:
            speak(item)
        except Exception as exc:
            emit({"failed": item["id"], "error": _error_text(exc)})
        else:
            emit({"done": item["id"]})


def main(argv=None) -> int:
    with open((argv or sys.argv)[1], encoding="utf-8") as f:
        spec = json.load(f)
    prefix = f"{PREFIX}{spec['nonce']} "

    def emit(event: dict) -> None:
        # Leading newline: a half-written library line must not swallow the prefix.
        sys.stdout.write("\n" + prefix + json.dumps(event) + "\n")
        sys.stdout.flush()

    run(spec, emit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
