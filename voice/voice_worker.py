"""voice/voice_worker.py -- the child that speaks a batch of lines inside a voice engine's own venv.

voice_venv.run_batch starts it as `<venv python> -I -X utf8 voice_worker.py <spec file>`.
It runs under a different interpreter whose packages clash with the app's (qwen-tts and
chatterbox-tts pin other transformers), so it imports nothing from the app and only the standard library
at module level; an engine's packages are imported by its adapter once the spec names it.

The model is loaded once per spec, then each item is written to its "out" path. Events
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


# Both engines can leave hiss or a stray syllable after the last word and end on a
# non-zero sample, which clicks. Everything quieter than this below the clip's peak
# after the last real speech is cut, a short natural tail is kept, and the end is faded.
TAIL_FLOOR_DB = -40.0
TAIL_FRAME_SECONDS = 0.01
TAIL_KEEP_SECONDS = 0.12
TAIL_FADE_SECONDS = 0.05


def _trim_tail(wav, rate: int):
    """`wav` as a flat float32 array with the post-speech tail trimmed and faded out."""
    import numpy

    samples = numpy.asarray(wav, dtype=numpy.float32).reshape(-1)
    peak = float(numpy.abs(samples).max()) if samples.size else 0.0
    if peak == 0.0:
        return samples
    frame = max(1, int(rate * TAIL_FRAME_SECONDS))
    usable = samples[: samples.size // frame * frame]
    if usable.size:
        rms = numpy.sqrt((usable.reshape(-1, frame) ** 2).mean(axis=1))
        loud = numpy.nonzero(rms > peak * 10 ** (TAIL_FLOOR_DB / 20))[0]
        if loud.size:
            end = min(samples.size, (int(loud[-1]) + 1) * frame + int(rate * TAIL_KEEP_SECONDS))
            samples = samples[:end]
    fade = min(samples.size, int(rate * TAIL_FADE_SECONDS))
    if fade:
        samples = samples.copy()
        samples[-fade:] *= numpy.linspace(1.0, 0.0, fade, dtype=numpy.float32)
    return samples


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

    def speak(items):
        first = items[0]
        # Building the clone prompt encodes the reference clip; a narration reuses a
        # handful of voices for hundreds of lines, so each is built once.
        key = (first["ref_audio"], first.get("ref_text") or "")
        if key not in prompts:
            prompts[key] = model.create_voice_clone_prompt(
                ref_audio=first["ref_audio"], ref_text=first.get("ref_text") or None,
                # Without the clip's transcript only the speaker embedding can be used.
                x_vector_only_mode=not first.get("ref_text"))
        language = QWEN3_TTS_LANGUAGES.get(first.get("language"), "Auto")
        wavs, rate = model.generate_voice_clone(
            text=[item["text"] for item in items], language=[language] * len(items),
            voice_clone_prompt=prompts[key] * len(items))
        _write_each(sf, items, wavs, rate)
    return speak


def _write_each(sf, items, wavs, rate) -> None:
    # A short answer would leave a line silent, or put one line's audio in another's file.
    if len(wavs) != len(items):
        raise RuntimeError(f"the engine returned {len(wavs)} clips for {len(items)} lines")
    for item, wav in zip(items, wavs):
        sf.write(item["out"], _trim_tail(wav, rate), rate)


# Chatterbox stops after 1000 speech tokens (about 40 s), dropping the rest of the text
# without an error, so a long narration unit is spoken in pieces well under that.
CHATTERBOX_MAX_CHARS_CJK = 120
CHATTERBOX_MAX_CHARS = 280
_SENTENCE_ENDS = "。！？；…!?;."


def _is_cjk(text: str) -> bool:
    return any("\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff"
               or "\uac00" <= ch <= "\ud7af" for ch in text)


def _chatterbox_pieces(text: str) -> list:
    """`text` cut at sentence ends into pieces of at most the cap (a sentence longer
    than that is cut at the cap)."""
    cap = CHATTERBOX_MAX_CHARS_CJK if _is_cjk(text) else CHATTERBOX_MAX_CHARS
    sentences, start = [], 0
    for i, ch in enumerate(text):
        if ch in _SENTENCE_ENDS:
            sentences.append(text[start:i + 1])
            start = i + 1
    sentences.append(text[start:])
    pieces, current = [], ""
    for sentence in sentences:
        while len(sentence) > cap:
            if current.strip():
                pieces.append(current)
            current = ""
            pieces.append(sentence[:cap])
            sentence = sentence[cap:]
        if len(current) + len(sentence) > cap and current.strip():
            pieces.append(current)
            current = ""
        current += sentence
    if current.strip():
        pieces.append(current)
    return [p.strip() for p in pieces if p.strip()]


def _load_chatterbox(spec: dict):
    import numpy
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    model = ChatterboxMultilingualTTS.from_pretrained(
        device="cuda" if torch.cuda.is_available() else "cpu")
    voices = {}

    def speak(items):
        (item,) = items
        # The model keeps one voice at a time; encoding a clip is the slow part, so each
        # voice's encoding is kept and swapped back in for its next line.
        if item["ref_audio"] not in voices:
            model.prepare_conditionals(item["ref_audio"])
            voices[item["ref_audio"]] = model.conds
        model.conds = voices[item["ref_audio"]]
        # Chatterbox takes the language codes the app uses (zh, ja, ko, en).
        parts = [_trim_tail(model.generate(piece, language_id=item.get("language") or "en")
                            .squeeze(0).numpy(), model.sr)
                 for piece in _chatterbox_pieces(item["text"]) or [item["text"]]]
        sf.write(item["out"], numpy.concatenate(parts) if len(parts) > 1 else parts[0], model.sr)
    return speak


OMNIVOICE_REPO = "k2-fsa/OmniVoice"


def _load_omnivoice(spec: dict):
    import soundfile as sf
    import torch
    from omnivoice import OmniVoice

    cuda = torch.cuda.is_available()
    model = OmniVoice.from_pretrained(
        OMNIVOICE_REPO, device_map="cuda:0" if cuda else "cpu",
        dtype=torch.float16 if cuda else torch.float32)
    prompts = {}

    def speak(items):
        first, kwargs = items[0], {}
        if first.get("ref_audio"):
            # generate() would encode the clip again for every call, and without the
            # clip's transcript also run its own speech recognition on it, so each
            # voice's prompt is built once, with the same defaults generate() uses.
            key = (first["ref_audio"], first.get("ref_text") or "")
            if key not in prompts:
                prompts[key] = model.create_voice_clone_prompt(
                    ref_audio=first["ref_audio"], ref_text=first.get("ref_text") or None)
            kwargs["voice_clone_prompt"] = [prompts[key]] * len(items)
        elif first.get("instruct"):
            kwargs["instruct"] = first["instruct"]
        audio = model.generate(text=[item["text"] for item in items], **kwargs)
        _write_each(sf, items, audio, OMNIVOICE_SAMPLE_RATE)
    return speak


OMNIVOICE_SAMPLE_RATE = 24000

# engine -> load(spec) returning speak(items), which writes each item's "out". Every
# item in one call shares a voice and a language (_voice_key).
ADAPTERS = {"omnivoice": _load_omnivoice, "qwen3_tts": _load_qwen3_tts,
            "chatterbox": _load_chatterbox}

# Lines spoken in one model call. Speaking two at once keeps the GPU busier than one
# short line can; Chatterbox's generate() takes one text, so it stays at one.
BATCH_SIZES = {"omnivoice": 2, "qwen3_tts": 2}
# A line waits for a partner in its voice only among the next few, so a run still goes
# through the book in order and a cancel leaves no gap far behind the last clip.
BATCH_LOOKAHEAD = 8
PAIR_FAILURES_BEFORE_SINGLES = 2


def _voice_key(item) -> tuple:
    return (item.get("ref_audio") or "", item.get("ref_text") or "", item.get("instruct") or "",
            item.get("language") or "")


def _batches(items: list, size: int):
    """`items` in order, cut into lists of up to `size` that share one _voice_key."""
    taken = [False] * len(items)
    for i, item in enumerate(items):
        if taken[i]:
            continue
        batch, key = [item], _voice_key(item)
        for j in range(i + 1, min(len(items), i + 1 + BATCH_LOOKAHEAD)):
            if len(batch) == size:
                break
            if not taken[j] and _voice_key(items[j]) == key:
                taken[j] = True
                batch.append(items[j])
        yield batch


def _out_of_memory(exc: BaseException) -> bool:
    text = str(exc).lower()
    return ("out of memory" in text or "alloc_failed" in text
            or type(exc).__name__ == "OutOfMemoryError")


def _free_gpu_cache() -> None:
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:ERROR_MAX_CHARS]


def run(spec: dict, emit, adapters=None) -> None:
    """Speaks every item in `spec["items"]`, reporting through `emit(event)`. One item
    failing does not stop the rest; a model that can't load stops the batch."""
    load = (ADAPTERS if adapters is None else adapters).get(spec.get("engine") or "")
    if load is None:
        emit({"fatal": "Unknown voice engine."})
        return
    try:
        speak = load(spec)
    except Exception as exc:
        emit({"fatal": _error_text(exc)})
        return
    emit({"loaded": True})
    size = BATCH_SIZES.get(spec.get("engine") or "", 1)
    failed_pairs = 0
    for batch in _batches(spec.get("items") or [], size):
        if len(batch) > 1 and size > 1:
            try:
                speak(batch)
            except Exception as exc:
                # A pair can fail where each line alone would not, so the pair is
                # retried one line at a time. Out of GPU memory, or after pairs keep
                # failing, the rest of the run goes one at a time too: three calls a
                # pair would eat into the batch's time limit.
                failed_pairs += 1
                if _out_of_memory(exc) or failed_pairs >= PAIR_FAILURES_BEFORE_SINGLES:
                    size = 1
                _free_gpu_cache()
            else:
                for item in batch:
                    emit({"done": item["id"]})
                continue
        for item in batch:
            try:
                speak([item])
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
