"""voice_worker.py -- the child that speaks a batch of lines inside a voice engine's own venv.

voice_venv.run_batch starts it as `<venv python> -I -X utf8 voice_worker.py <spec file>`.
It runs under a different interpreter whose packages clash with the app's (qwen-tts and
chatterbox-tts pin other transformers), so it imports nothing from the app and only the standard library
at module level; an engine's packages are imported by its adapter once the spec names it.

The model is loaded once per batch, then each item is written to its "out" path. Events
go to stdout as `@@voice-<nonce> {json}` lines, one per event; anything else on the stream
is library noise and the parent ignores it. Events: {"loaded": true} once the model is up,
{"done": id} or {"failed": id, "error": text} per item, {"fatal": text} when nothing more
can be spoken.
"""

import json
import os
import sys
import threading
import time

PREFIX = "@@voice-"
ERROR_MAX_CHARS = 300
PARENT_CHECK_SECONDS = 2.0

QWEN3_TTS_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
# qwen-tts takes language names, not codes.
QWEN3_TTS_LANGUAGES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English"}


def _load_qwen3_tts(spec: dict):
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    cuda = torch.cuda.is_available()
    # Cards before compute 8.0 (a V100 is 7.0) have no native bfloat16, and
    # is_bf16_supported() says yes to them by counting slow emulation; half precision
    # runs there.
    if not cuda:
        dtype = torch.float32
    else:
        dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
    model = Qwen3TTSModel.from_pretrained(
        spec.get("options", {}).get("model") or QWEN3_TTS_MODEL,
        device_map="cuda:0" if cuda else "cpu", dtype=dtype)
    prompts = {}

    def speak(item):
        # Building the clone prompt encodes the reference clip; a narration reuses a
        # handful of voices for hundreds of lines, so each is built once.
        key = (item["ref_audio"], item.get("ref_text") or "")
        if key not in prompts:
            prompts[key] = model.create_voice_clone_prompt(
                ref_audio=item["ref_audio"], ref_text=item.get("ref_text") or None,
                # Without the clip's transcript only the speaker embedding can be used.
                x_vector_only_mode=not item.get("ref_text"))
        wavs, rate = model.generate_voice_clone(
            text=item["text"], language=QWEN3_TTS_LANGUAGES.get(item.get("language"), "Auto"),
            voice_clone_prompt=prompts[key])
        sf.write(item["out"], wavs[0], rate)
    return speak


def _load_chatterbox(spec: dict):
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    model = ChatterboxMultilingualTTS.from_pretrained(
        device="cuda" if torch.cuda.is_available() else "cpu")
    voices = {}

    def speak(item):
        # The model keeps one voice at a time; encoding a clip is the slow part, so each
        # voice's encoding is kept and swapped back in for its next line.
        if item["ref_audio"] not in voices:
            model.prepare_conditionals(item["ref_audio"])
            voices[item["ref_audio"]] = model.conds
        model.conds = voices[item["ref_audio"]]
        # Chatterbox takes the language codes the app uses (zh, ja, ko, en).
        wav = model.generate(item["text"], language_id=item.get("language") or "en")
        sf.write(item["out"], wav.squeeze(0).numpy(), model.sr)
    return speak


# engine -> load(spec) returning speak(item), which writes item["out"].
ADAPTERS = {"qwen3_tts": _load_qwen3_tts, "chatterbox": _load_chatterbox}


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


def _exit_when_orphaned(write) -> None:
    # A killed app or job worker must not leave a model on the GPU with nobody to read
    # its output, and not every way the parent ends kills this process (on Windows a
    # dead parent kills nothing, and this process's parent is the venv's launcher).
    # Only the parent holds the pipe's read end, so a write fails once it is gone.
    while True:
        time.sleep(PARENT_CHECK_SECONDS)
        try:
            write("\n")
        except (OSError, ValueError):
            os._exit(1)


def main(argv=None) -> int:
    with open((argv or sys.argv)[1], encoding="utf-8") as f:
        spec = json.load(f)
    prefix = f"{PREFIX}{spec['nonce']} "
    lock = threading.Lock()

    def write(text: str) -> None:
        with lock:
            sys.stdout.write(text)
            sys.stdout.flush()

    def emit(event: dict) -> None:
        # Leading newline: a half-written library line must not swallow the prefix.
        write("\n" + prefix + json.dumps(event) + "\n")

    threading.Thread(target=_exit_when_orphaned, args=(write,), daemon=True).start()

    run(spec, emit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
